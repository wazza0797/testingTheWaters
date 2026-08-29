from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timedelta
from decimal import Decimal

import typer
import uvicorn
import yaml

from trading_platform import __version__
from trading_platform.analytics.report import PerformanceReport, build_performance_report
from trading_platform.backtesting.optimization import score_result
from trading_platform.backtesting.result import BacktestResult
from trading_platform.backtesting.validation import HoldOutValidator
from trading_platform.backtesting.walk_forward import WalkForwardResult, WalkForwardRunner
from trading_platform.config.loader import AnalyticsConfig, load_config
from trading_platform.config.settings import Environment, Settings
from trading_platform.container import (
    AppContainer,
    build_backtest_engine,
    build_container,
    build_demo_session,
    build_paper_session,
)
from trading_platform.domain.errors import TradingPlatformError
from trading_platform.domain.events.system import Heartbeat
from trading_platform.domain.models.bar import Bar
from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.market_data.gaps import find_gaps
from trading_platform.market_data.timeframe import timeframe_to_timedelta
from trading_platform.utils.logging import configure_logging
from trading_platform.utils.time import to_utc, utc_now

_HEARTBEAT_INTERVAL_SECONDS = 10.0

app = typer.Typer(
    name="trading-platform",
    help="Modular algorithmic trading platform — crypto-first, exchange-agnostic.",
    no_args_is_help=True,
)

logger = logging.getLogger(__name__)


def _warn_on_gaps(bars: list[Bar], timeframe: str) -> None:
    """Print a warning (never fails the command) if `bars` has any stretches
    spaced further apart than one `timeframe` interval — see
    `market_data/gaps.py`. A silent gap in downloaded/backtested history can
    quietly skew results without any indication anything is wrong, which is
    exactly the kind of thing worth surfacing loudly instead.
    """
    gaps = find_gaps(bars, timeframe)
    if not gaps:
        return
    total_missing = sum(gap.missing_count for gap in gaps)
    typer.echo(
        f"WARNING: {len(gaps)} gap(s) found in {timeframe} data "
        f"(~{total_missing} missing bar(s) total). This can bias backtest results "
        "— treat conclusions from this range with caution.",
        err=True,
    )
    for gap in gaps[:10]:
        typer.echo(
            f"  gap: {gap.after.isoformat()} -> {gap.before.isoformat()} "
            f"(~{gap.missing_count} missing bar(s))",
            err=True,
        )
    if len(gaps) > 10:
        typer.echo(f"  ... and {len(gaps) - 10} more gap(s).", err=True)


def _load_logger_overrides() -> dict[str, str]:
    try:
        with open("config/logging.yaml", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}
    overrides = data.get("loggers", {})
    return dict(overrides) if isinstance(overrides, dict) else {}


def _bootstrap(overlay: str | None = None) -> AppContainer:
    settings = Settings()
    settings.require_live_trading_confirmed()
    configure_logging(
        level=settings.log_level,
        fmt=settings.log_format,
        logger_overrides=_load_logger_overrides(),
    )
    config = load_config(overlay=overlay)
    return build_container(settings, config)


async def _run_observability_servers(container: AppContainer, host: str = "0.0.0.0") -> None:
    fastapi_app = container.observability_app()
    ports = {container.settings.metrics_port, container.settings.health_port}
    servers = [
        uvicorn.Server(uvicorn.Config(fastapi_app, host=host, port=port, log_level="warning"))
        for port in sorted(ports)
    ]
    await asyncio.gather(*(server.serve() for server in servers))


@app.command()
def serve(
    overlay: str | None = typer.Option(
        None,
        "--overlay",
        help="Named config overlay to merge over default.yaml (e.g. 'backtest', 'paper').",
    ),
) -> None:
    """Run the observability HTTP server (/health, /metrics) with background pollers.

    This is the Milestone 0 entrypoint proving the event bus + metrics
    pipeline works end-to-end. The `backtest`/`paper`/`live` subcommands below
    are stubs — they land in Milestones 4/6/8 and will reuse this same
    container/event bus wiring.
    """
    container = _bootstrap(overlay)
    stop_event = threading.Event()

    def _background_loop() -> None:
        last_heartbeat = 0.0
        while not stop_event.is_set():
            if container.settings.observability_enabled:
                container.system_monitor.poll_once()
                if container.config.observability.log_summary_enabled:
                    container.summary_logger.maybe_emit()

            now = time.monotonic()
            if now - last_heartbeat >= _HEARTBEAT_INTERVAL_SECONDS:
                # No strategy/risk/execution pipeline exists yet at this
                # milestone — Heartbeat exercises the TimedEventBus + metrics
                # pipeline end-to-end so /metrics shows real handler latency.
                container.event_bus.publish(
                    Heartbeat(mode="observability", uptime_seconds=container.health.uptime_seconds)
                )
                last_heartbeat = now

            time.sleep(1.0)

    poller_thread = threading.Thread(
        target=_background_loop, name="observability-poller", daemon=True
    )
    poller_thread.start()

    logger.info(
        "observability_server_starting",
        extra={
            "metrics_port": container.settings.metrics_port,
            "health_port": container.settings.health_port,
        },
    )
    try:
        asyncio.run(_run_observability_servers(container))
    finally:
        stop_event.set()


@app.command(name="download-data")
def download_data(
    symbol: str | None = typer.Option(
        None, "--symbol", help="e.g. BTC/USDT (default: config trading.symbol)"
    ),
    timeframe: str | None = typer.Option(
        None, "--timeframe", help="e.g. 1h (default: config trading.timeframe)"
    ),
    days: int = typer.Option(365, "--days", help="Days of history to backfill from today."),
) -> None:
    """Download historical OHLCV bars and cache instrument rules (Milestone 1)."""
    container = _bootstrap()
    resolved_symbol = symbol or container.config.trading.symbol
    resolved_timeframe = timeframe or container.config.trading.timeframe
    exchange_name = container.exchange_adapter.exchange_name

    try:
        rules = container.instrument_rules_cache.load(exchange_name, resolved_symbol)
        if rules is None:
            typer.echo(f"Fetching instrument rules for {resolved_symbol} from {exchange_name}...")
            rules = container.exchange_adapter.fetch_instrument_rules(resolved_symbol)
            container.instrument_rules_cache.save(rules)
        typer.echo(
            f"Instrument rules: tick_size={rules.tick_size} step_size={rules.step_size} "
            f"min_qty={rules.min_qty} min_notional={rules.min_notional} "
            f"maker_fee={rules.maker_fee_rate} taker_fee={rules.taker_fee_rate}"
        )

        since = utc_now() - timedelta(days=days)
        typer.echo(
            f"Downloading {resolved_symbol}@{resolved_timeframe} since {since.isoformat()}..."
        )
        new_bars = container.data_ingest_service.sync(resolved_symbol, resolved_timeframe, since)
        typer.echo(
            f"Done. {new_bars} new bar(s) persisted for {resolved_symbol}@{resolved_timeframe}."
        )

        all_bars = list(
            container.market_data_repository.load_bars(resolved_symbol, resolved_timeframe)
        )
        _warn_on_gaps(all_bars, resolved_timeframe)
    except TradingPlatformError as exc:
        typer.echo(f"Download failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _build_report(
    result: BacktestResult,
    bars: list[Bar],
    analytics: AnalyticsConfig,
) -> PerformanceReport:
    return build_performance_report(
        result,
        bars,
        min_round_trips=analytics.min_round_trips,
        min_bars=analytics.min_bars,
        min_daily_returns_for_sharpe=analytics.min_daily_returns_for_sharpe,
        bootstrap_iterations=analytics.bootstrap_iterations,
        bootstrap_seed=analytics.bootstrap_seed,
        market_sma_period=analytics.market_sma_period,
    )


def _fmt_decimal_pct(value: Decimal | float | None, *, signed: bool = False) -> str:
    if value is None:
        return "n/a"
    number = float(value)
    if signed:
        return f"{number:+.2f}%"
    return f"{number:.2f}%"


def _print_performance_report(
    title: str, result: BacktestResult, report: PerformanceReport
) -> None:
    m = report.metrics
    typer.echo("")
    typer.echo(f"=== {title} ===")
    typer.echo(f"Bars processed:     {m.bars_processed}")
    typer.echo(f"Round-trips:        {m.round_trip_count}")
    typer.echo(f"Fills:              {len(result.fills)}")
    typer.echo(f"Starting cash:      {result.starting_cash}")
    typer.echo(f"Ending cash:        {result.ending_cash}")
    typer.echo(f"Ending equity:      {m.ending_equity}")
    typer.echo(f"Total return:       {_fmt_decimal_pct(m.total_return_pct)}")
    typer.echo(f"Max drawdown:       {_fmt_decimal_pct(m.max_drawdown_pct)}")
    sharpe = f"{m.sharpe_daily:.2f}" if m.sharpe_daily is not None else "n/a"
    typer.echo(f"Sharpe (daily):     {sharpe}")
    if m.win_rate is not None:
        typer.echo(
            f"Win rate:           {m.win_rate * 100:.1f}%  ({m.win_count}W / {m.loss_count}L)"
        )
    else:
        typer.echo("Win rate:           n/a")
    if m.profit_factor is None:
        pf = "n/a"
    elif m.profit_factor == float("inf"):
        pf = "inf"
    else:
        pf = f"{m.profit_factor:.2f}"
    typer.echo(f"Profit factor:      {pf}")
    avg = f"{m.avg_trade_pnl}" if m.avg_trade_pnl is not None else "n/a"
    typer.echo(f"Avg trade PnL:      {avg}")
    typer.echo(f"Total fees paid:    {m.total_fees}")
    if report.buy_and_hold_return_pct is not None:
        typer.echo(
            f"Buy & hold return:  {_fmt_decimal_pct(report.buy_and_hold_return_pct, signed=True)}"
        )
    typer.echo(f"Final position:     {result.final_position}")

    for flag in report.flags:
        typer.echo(f"⚠ {flag.flag.value}: {flag.message}")

    if report.calendar_quarters:
        typer.echo("")
        typer.echo("=== Regime Breakdown (calendar quarters) ===")
        typer.echo(f"{'Period':<10} {'Return':>8} {'MaxDD':>8} {'Trips':>7} {'B&H':>8}")
        for row in report.calendar_quarters:
            bh = _fmt_decimal_pct(row.buy_and_hold_return_pct, signed=True)
            typer.echo(
                f"{row.label:<10} "
                f"{_fmt_decimal_pct(row.return_pct, signed=True):>8} "
                f"{_fmt_decimal_pct(row.max_drawdown_pct):>8} "
                f"{row.round_trip_count:>7} "
                f"{bh:>8}"
            )

    if report.market_regimes:
        typer.echo("")
        typer.echo("=== Regime Breakdown (market) ===")
        typer.echo(f"{'Regime':<10} {'Return':>8} {'MaxDD':>8} {'Trips':>7} {'B&H':>8}")
        for row in report.market_regimes:
            bh = _fmt_decimal_pct(row.buy_and_hold_return_pct, signed=True)
            typer.echo(
                f"{row.label:<10} "
                f"{_fmt_decimal_pct(row.return_pct, signed=True):>8} "
                f"{_fmt_decimal_pct(row.max_drawdown_pct):>8} "
                f"{row.round_trip_count:>7} "
                f"{bh:>8}"
            )


def _emit_report_json(reports: dict[str, PerformanceReport]) -> None:
    payload = {name: report.to_dict() for name, report in reports.items()}
    typer.echo("")
    typer.echo(json.dumps(payload, indent=2))


def _run_single_backtest(
    container: AppContainer,
    rules: InstrumentRules,
    bars: list[Bar],
    *,
    symbol: str,
    timeframe: str,
    report_format: str | None,
) -> None:
    run = build_backtest_engine(container, rules, symbol=symbol, timeframe=timeframe)
    typer.echo(
        f"Backtesting {run.symbol}@{run.timeframe} over {len(bars)} bar(s) "
        f"({bars[0].timestamp.isoformat()} -> {bars[-1].timestamp.isoformat()})..."
    )
    try:
        result = run.engine.run(bars, timeframe)
    finally:
        run.teardown()
    report = _build_report(result, bars, container.config.analytics)
    _print_performance_report("Backtest Result", result, report)
    if report_format == "json":
        _emit_report_json({"backtest": report})


def _run_hold_out_backtest(
    container: AppContainer,
    rules: InstrumentRules,
    bars: list[Bar],
    *,
    symbol: str,
    timeframe: str,
    report_format: str | None,
) -> None:
    validation = container.config.validation
    assert validation.train_end is not None and validation.test_start is not None

    train_end = to_utc(validation.train_end)
    test_start = to_utc(validation.test_start)
    test_end = to_utc(validation.test_end) if validation.test_end is not None else None

    typer.echo(
        "Hold-out validation enabled — running in-sample then out-of-sample.\n"
        "OOS results are the only ones that count for strategy validation; "
        "IS is for tuning only."
    )
    typer.echo(
        f"IS:  timestamp < {train_end.isoformat()}  |  "
        f"OOS: timestamp >= {test_start.isoformat()}"
        + (f" and < {test_end.isoformat()}" if test_end else "")
    )

    validator = HoldOutValidator(
        lambda: build_backtest_engine(container, rules, symbol=symbol, timeframe=timeframe)
    )
    hold_out = validator.run(
        bars,
        timeframe,
        train_end=train_end,
        test_start=test_start,
        test_end=test_end,
    )

    is_bars = [b for b in bars if b.timestamp < train_end]
    oos_bars = [
        b
        for b in bars
        if b.timestamp >= test_start and (test_end is None or b.timestamp < test_end)
    ]
    analytics = container.config.analytics
    is_report = _build_report(hold_out.is_result, is_bars, analytics)
    oos_report = _build_report(hold_out.oos_result, oos_bars, analytics)
    _print_performance_report("In-Sample (tuning only)", hold_out.is_result, is_report)
    _print_performance_report("Out-of-Sample (validation)", hold_out.oos_result, oos_report)
    if report_format == "json":
        _emit_report_json({"in_sample": is_report, "out_of_sample": oos_report})


@app.command()
def backtest(
    symbol: str | None = typer.Option(
        None, "--symbol", help="e.g. BTC/USDT (default: config trading.symbol)"
    ),
    timeframe: str | None = typer.Option(
        None, "--timeframe", help="e.g. 1h, 4h, 1d (default: config trading.timeframe)"
    ),
    start: str | None = typer.Option(
        None, "--start", help="ISO date/datetime to start from (default: earliest cached bar)."
    ),
    end: str | None = typer.Option(
        None, "--end", help="ISO date/datetime to end at, exclusive (default: latest cached bar)."
    ),
    report: str | None = typer.Option(
        None,
        "--report",
        help="Optional machine-readable output: 'json' prints PerformanceReport JSON after the summary.",
    ),
) -> None:
    """Replay cached historical bars through strategy -> risk -> execution
    with realistic simulated fills, and print performance analytics
    (Milestone 4 / 4.5 / 5).

    When `validation.enabled` is true in config/backtest.yaml, runs an
    in-sample then out-of-sample hold-out and prints both reports.

    Requires `trading-platform download-data` to have been run first for the
    chosen symbol/timeframe — this command never talks to an exchange.
    """
    if report is not None and report != "json":
        typer.echo(f"Unsupported --report value '{report}' (use 'json').", err=True)
        raise typer.Exit(code=1)

    container = _bootstrap(overlay="backtest")
    resolved_symbol = symbol or container.config.trading.symbol
    resolved_timeframe = timeframe or container.config.trading.timeframe
    exchange_name = container.exchange_adapter.exchange_name

    try:
        if timeframe is not None:
            timeframe_to_timedelta(resolved_timeframe)  # validate early (e.g. reject "1x")

        rules = container.instrument_rules_cache.load(exchange_name, resolved_symbol)
        if rules is None:
            typer.echo(
                f"No cached instrument rules for {resolved_symbol} on {exchange_name}. "
                "Run 'trading-platform download-data' first.",
                err=True,
            )
            raise typer.Exit(code=1)

        try:
            start_dt = to_utc(datetime.fromisoformat(start)) if start else None
            end_dt = to_utc(datetime.fromisoformat(end)) if end else None
        except ValueError as exc:
            typer.echo(f"Invalid --start/--end value: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        bars = list(
            container.market_data_repository.load_bars(
                resolved_symbol, resolved_timeframe, start_dt, end_dt
            )
        )
        if not bars:
            typer.echo(
                f"No cached bars for {resolved_symbol}@{resolved_timeframe} in the "
                "requested range. Run 'trading-platform download-data' first "
                f"(e.g. --symbol {resolved_symbol} --timeframe {resolved_timeframe}).",
                err=True,
            )
            raise typer.Exit(code=1)

        _warn_on_gaps(bars, resolved_timeframe)

        if container.config.validation.enabled:
            _run_hold_out_backtest(
                container,
                rules,
                bars,
                symbol=resolved_symbol,
                timeframe=resolved_timeframe,
                report_format=report,
            )
        else:
            _run_single_backtest(
                container,
                rules,
                bars,
                symbol=resolved_symbol,
                timeframe=resolved_timeframe,
                report_format=report,
            )
    except TradingPlatformError as exc:
        typer.echo(f"Backtest failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _print_walk_forward_result(result: WalkForwardResult) -> None:
    typer.echo("")
    typer.echo("=== Walk-Forward Result ===")
    typer.echo(f"Folds:              {result.fold_count}")
    typer.echo(f"Objective:          {result.objective}")
    typer.echo(
        f"{'Fold':<6} {'IS bars':>10} {'OOS bars':>10} "
        f"{'OOS ret%':>10} {'OOS Sharpe':>11}  Best params"
    )
    for fold in result.folds:
        oos = fold.oos_result
        oos_sharpe = score_result(oos, "sharpe_daily")
        sharpe_s = "n/a" if oos_sharpe == float("-inf") else f"{oos_sharpe:.2f}"
        params_s = ", ".join(f"{k}={v}" for k, v in sorted(fold.best_params.items()))
        typer.echo(
            f"{fold.fold_index:<6} "
            f"{fold.is_end_index - fold.is_start_index:>10} "
            f"{fold.oos_end_index - fold.oos_start_index:>10} "
            f"{float(oos.total_return_pct):>+10.2f} "
            f"{sharpe_s:>11}  {params_s}"
        )

    if result.stitched_oos_equity:
        start_eq = result.stitched_oos_equity[0].equity
        end_eq = result.stitched_oos_equity[-1].equity
        stitched_ret = (
            Decimal("0") if start_eq == 0 else (end_eq - start_eq) / start_eq * Decimal("100")
        )
        typer.echo("")
        typer.echo("=== Stitched OOS Equity (OOS segments only) ===")
        typer.echo(f"Points:             {len(result.stitched_oos_equity)}")
        typer.echo(f"Starting equity:    {start_eq}")
        typer.echo(f"Ending equity:      {end_eq}")
        typer.echo(f"Compounded return:  {_fmt_decimal_pct(stitched_ret, signed=True)}")


@app.command("walk-forward")
def walk_forward(
    symbol: str | None = typer.Option(
        None, "--symbol", help="e.g. BTC/USDT (default: config trading.symbol)"
    ),
    timeframe: str | None = typer.Option(
        None, "--timeframe", help="e.g. 1h, 4h, 1d (default: config trading.timeframe)"
    ),
    start: str | None = typer.Option(
        None, "--start", help="ISO date/datetime to start from (default: earliest cached bar)."
    ),
    end: str | None = typer.Option(
        None, "--end", help="ISO date/datetime to end at, exclusive (default: latest cached bar)."
    ),
) -> None:
    """Run rolling walk-forward optimization (Milestone 4.5 Phase C).

    For each fold: grid-search strategy params on the in-sample window, freeze
    the winner, evaluate on the following out-of-sample window. Prints a fold
    table and a stitched OOS equity curve (OOS segments only).

    Configure windows / param_grid / objective under
    `validation.walk_forward` in config/backtest.yaml.
    """
    container = _bootstrap(overlay="backtest")
    resolved_symbol = symbol or container.config.trading.symbol
    resolved_timeframe = timeframe or container.config.trading.timeframe
    exchange_name = container.exchange_adapter.exchange_name
    wf = container.config.validation.walk_forward

    try:
        if not wf.param_grid:
            typer.echo(
                "walk_forward.param_grid is empty — set candidate values in "
                "config/backtest.yaml under validation.walk_forward.param_grid.",
                err=True,
            )
            raise typer.Exit(code=1)

        if timeframe is not None:
            timeframe_to_timedelta(resolved_timeframe)

        rules = container.instrument_rules_cache.load(exchange_name, resolved_symbol)
        if rules is None:
            typer.echo(
                f"No cached instrument rules for {resolved_symbol} on {exchange_name}. "
                "Run 'trading-platform download-data' first.",
                err=True,
            )
            raise typer.Exit(code=1)

        try:
            start_dt = to_utc(datetime.fromisoformat(start)) if start else None
            end_dt = to_utc(datetime.fromisoformat(end)) if end else None
        except ValueError as exc:
            typer.echo(f"Invalid --start/--end value: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        bars = list(
            container.market_data_repository.load_bars(
                resolved_symbol, resolved_timeframe, start_dt, end_dt
            )
        )
        if not bars:
            typer.echo(
                f"No cached bars for {resolved_symbol}@{resolved_timeframe} in the "
                "requested range. Run 'trading-platform download-data' first.",
                err=True,
            )
            raise typer.Exit(code=1)

        _warn_on_gaps(bars, resolved_timeframe)

        n_combos = 1
        for values in wf.param_grid.values():
            n_combos *= max(len(values), 1)
        typer.echo(
            f"Walk-forward {resolved_symbol}@{resolved_timeframe}: "
            f"{len(bars)} bar(s), IS={wf.is_bars}, OOS={wf.oos_bars}, "
            f"step={wf.step_bars}, grid={n_combos} combo(s), "
            f"objective={wf.objective}..."
        )

        runner = WalkForwardRunner(
            lambda params: build_backtest_engine(
                container,
                rules,
                symbol=resolved_symbol,
                timeframe=resolved_timeframe,
                strategy_params=params,
            ),
            is_bars=wf.is_bars,
            oos_bars=wf.oos_bars,
            step_bars=wf.step_bars,
            param_grid=wf.param_grid,
            objective=wf.objective,
            starting_cash=container.config.backtest.starting_cash,
        )
        result = runner.run(bars, resolved_timeframe)
        _print_walk_forward_result(result)
    except TradingPlatformError as exc:
        typer.echo(f"Walk-forward failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def paper(
    symbol: str | None = typer.Option(
        None, "--symbol", help="e.g. BTC/USDT (default: config trading.symbol)"
    ),
    timeframe: str | None = typer.Option(
        None, "--timeframe", help="e.g. 1h (default: config trading.timeframe)"
    ),
) -> None:
    """Run paper trading: poll live closed candles with virtual cash (Milestone 6).

    Uses the same fill simulation as backtests (spread/fees/latency). Cash and
    positions persist to DATA_DIR/paper_state.json so restarts continue the
    session. Press Ctrl+C to stop gracefully.
    """
    container = _bootstrap(overlay="paper")
    resolved_symbol = symbol or container.config.trading.symbol
    resolved_timeframe = timeframe or container.config.trading.timeframe
    exchange_name = container.exchange_adapter.exchange_name

    try:
        if timeframe is not None:
            timeframe_to_timedelta(resolved_timeframe)

        rules = container.instrument_rules_cache.load(exchange_name, resolved_symbol)
        if rules is None:
            typer.echo(
                f"No cached instrument rules for {resolved_symbol} on {exchange_name}. "
                "Run 'trading-platform download-data' first (rules are cached locally).",
                err=True,
            )
            raise typer.Exit(code=1)

        session = build_paper_session(
            container,
            rules,
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            on_heartbeat=typer.echo,
        )
        portfolio = session.portfolio_handler
        typer.echo(
            f"Paper trading {session.symbol}@{session.timeframe} — "
            f"cash={portfolio.cash}, state={session.state_path}"
        )
        if portfolio.last_bar_timestamp is not None:
            typer.echo(f"Resuming after bar {portfolio.last_bar_timestamp.isoformat()}")
        typer.echo("Polling for closed candles (Ctrl+C to stop)...")
        try:
            bars = session.loop.run()
        finally:
            session.teardown()

        typer.echo(
            f"Stopped. bars_processed={bars}, cash={portfolio.cash}, "
            f"position={portfolio.position_for(session.symbol)}"
        )
    except TradingPlatformError as exc:
        typer.echo(f"Paper trading failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def demo(
    symbol: str | None = typer.Option(
        None, "--symbol", help="e.g. BTC/USDT (default: config trading.symbol)"
    ),
    timeframe: str | None = typer.Option(
        None, "--timeframe", help="e.g. 1h (default: config trading.timeframe)"
    ),
) -> None:
    """Run exchange demo/practice trading (Milestone 8a).

    Requires ENV=demo and venue demo API keys (e.g. BINANCE_DEMO_API_KEY /
    BINANCE_DEMO_API_SECRET). Cash and positions are read from the exchange
    account — not a local starting_cash. Orders go to the sandbox selected by
    trading.exchange. Press Ctrl+C to stop.
    """
    container = _bootstrap(overlay="demo")
    if container.settings.environment != Environment.DEMO:
        typer.echo(
            "demo requires ENV=demo in .env (and BINANCE_DEMO_* keys for Binance).",
            err=True,
        )
        raise typer.Exit(code=1)

    resolved_symbol = symbol or container.config.trading.symbol
    resolved_timeframe = timeframe or container.config.trading.timeframe
    exchange_name = container.config.trading.exchange

    try:
        if timeframe is not None:
            timeframe_to_timedelta(resolved_timeframe)

        rules = container.instrument_rules_cache.load(exchange_name, resolved_symbol)
        if rules is None:
            typer.echo(
                f"No cached instrument rules for {resolved_symbol} on {exchange_name}. "
                "Run 'trading-platform download-data' first (rules are cached locally).",
                err=True,
            )
            raise typer.Exit(code=1)

        session = build_demo_session(
            container,
            rules,
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            on_heartbeat=typer.echo,
        )
        portfolio = session.portfolio_handler
        typer.echo(
            f"Demo trading on {exchange_name}: {session.symbol}@{session.timeframe} — "
            f"cash={portfolio.cash} (from exchange), "
            f"position={portfolio.position_for(session.symbol)}, "
            f"state={session.state_path}"
        )
        if portfolio.last_bar_timestamp is not None:
            typer.echo(f"Resuming bar cursor after {portfolio.last_bar_timestamp.isoformat()}")
        typer.echo("Polling fills + closed candles (Ctrl+C to stop)...")
        try:
            bars = session.loop.run()
        finally:
            session.teardown()

        typer.echo(
            f"Stopped. bars_processed={bars}, cash={portfolio.cash}, "
            f"position={portfolio.position_for(session.symbol)}"
        )
    except TradingPlatformError as exc:
        typer.echo(f"Demo trading failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def version() -> None:
    """Print the installed trading-platform version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
