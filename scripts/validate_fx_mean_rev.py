#!/usr/bin/env python3
"""Full-history + rolling-OOS validation for FX mean-reversion screen hits.

Candidates (quick screen on trailing 2500 bars; both beat B&H on that window):

  1. EURUSD 1h RSI 14 / 30–70, ADX any, stop=none, min_regime_bars=3, L/S
  2. EURCHF 4h BB 10 / 2.5σ, ADX any, stop=none, min_regime_bars=3, L/S

Checks per candidate:
1. Full-history backtest (all Yahoo stand-in bars).
2. Fixed-param rolling OOS (no IS grid) — does *this* candidate generalize?

Overnight funding is **not** modelled; hold-duration stats are printed.

Usage:
    uv run python scripts/validate_fx_mean_rev.py
    uv run python scripts/validate_fx_mean_rev.py --only eurusd
    uv run python scripts/validate_fx_mean_rev.py --only eurchf --no-discord
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from trading_platform.analytics.report import build_performance_report
from trading_platform.analytics.trades import reconstruct_round_trips
from trading_platform.backtesting.walk_forward import iter_walk_forward_windows, stitch_oos_equity
from trading_platform.config.loader import StrategyConfig, load_config
from trading_platform.config.settings import Settings
from trading_platform.container import build_backtest_engine, build_container
from trading_platform.market_data.instrument_rules_cache import InstrumentRulesCache
from trading_platform.market_data.repository.parquet import ParquetMarketDataRepository
from trading_platform.notifications.research import notify_demo_research

_REGIME_STRATEGY_PATH = "trading_platform.strategies.examples.regime_router:RegimeRouterStrategy"
_LOOKBACK = 120


@dataclass(frozen=True, slots=True)
class Candidate:
    key: str
    overlay: str
    symbol: str
    yahoo: str
    timeframe: str
    label: str
    params: dict[str, Any]
    # ~90d IS spacer / ~30d OOS at ~17 FX 1h bars/day; 4h scaled ~4×.
    is_bars: int
    oos_bars: int
    step_bars: int


def _always_on() -> dict[str, Any]:
    return {"compare": {"indicator": "sma", "period": 1, "op": ">", "value": 0.0}}


def _rsi_playbook(period: int, low: float, high: float) -> dict[str, Any]:
    mid = (low + high) / 2.0
    rsi = {"indicator": "rsi", "period": period}
    return {
        "long_entry": {"cross": {"left": rsi, "right": low, "direction": "above"}},
        "long_exit": {"cross": {"left": rsi, "right": mid, "direction": "above"}},
        "short_entry": {"cross": {"left": rsi, "right": high, "direction": "below"}},
        "short_exit": {"cross": {"left": rsi, "right": mid, "direction": "below"}},
    }


def _bb_playbook(period: int, num_std: float) -> dict[str, Any]:
    lower = {"indicator": "bb_lower", "period": period, "num_std": num_std}
    mid = {"indicator": "bb_mid", "period": period, "num_std": num_std}
    upper = {"indicator": "bb_upper", "period": period, "num_std": num_std}
    return {
        "long_entry": {"compare_indicators": {"left": "close", "right": lower, "op": "<="}},
        "long_exit": {"compare_indicators": {"left": "close", "right": mid, "op": ">="}},
        "short_entry": {"compare_indicators": {"left": "close", "right": upper, "op": ">="}},
        "short_exit": {"compare_indicators": {"left": "close", "right": mid, "op": "<="}},
    }


def _regime(playbook: dict[str, Any], *, min_regime_bars: int) -> dict[str, Any]:
    return {
        "default": "flat",
        "min_regime_bars": min_regime_bars,
        "lookback": _LOOKBACK,
        "regimes": [{"name": "mr", "when": _always_on(), "playbook": playbook}],
    }


_CANDIDATES: list[Candidate] = [
    Candidate(
        key="eurusd",
        overlay="ig-eurusd",
        symbol="CS.D.EURUSD.MINI.IP",
        yahoo="EURUSD=X",
        timeframe="1h",
        label="rsi14_30_70|adxany|stop=none|min=3|ls",
        params=_regime(_rsi_playbook(14, 30.0, 70.0), min_regime_bars=3),
        is_bars=1500,
        oos_bars=500,
        step_bars=1000,
    ),
    Candidate(
        key="eurchf",
        overlay="ig-eurchf",
        symbol="CS.D.EURCHF.MINI.IP",
        yahoo="EURCHF=X",
        timeframe="4h",
        label="bb10_2.5|adxany|stop=none|min=3|ls",
        params=_regime(_bb_playbook(10, 2.5), min_regime_bars=3),
        # ~180d IS / ~60d OOS at ~4.3 FX 4h bars/day.
        is_bars=750,
        oos_bars=250,
        step_bars=500,
    ),
]


def _print_metrics(label: str, report: Any, *, bh: Decimal | None) -> None:
    m = report.metrics
    sharpe = f"{m.sharpe_daily:.2f}" if m.sharpe_daily is not None else "n/a"
    win = f"{m.win_rate * 100:.1f}%" if m.win_rate is not None else "n/a"
    pf = f"{m.profit_factor:.2f}" if m.profit_factor is not None else "n/a"
    bh_s = f"{float(bh):+.2f}%" if bh is not None else "n/a"
    vs = ""
    if bh is not None:
        vs = f"  vs_bh={float(m.total_return_pct) - float(bh):+.2f}%"
    print(f"=== {label} ===")
    print(
        f"return={float(m.total_return_pct):+.2f}%  maxdd={float(m.max_drawdown_pct):.2f}%  "
        f"sharpe={sharpe}  trips={m.round_trip_count}  win={win}  pf={pf}  bh={bh_s}{vs}"
    )
    if report.flags:
        for flag in report.flags:
            print(f"  ! {flag.flag.value}: {flag.message}")
    print()


def _validate_one(candidate: Candidate, *, discord: bool) -> str:
    settings = Settings()
    base = load_config(overlay=candidate.overlay)
    config = base.model_copy(
        update={
            "strategy": StrategyConfig(path=_REGIME_STRATEGY_PATH, params={}),
            "trading": base.trading.model_copy(update={"timeframe": candidate.timeframe}),
        }
    )
    container = build_container(settings, config)
    repo = ParquetMarketDataRepository(Path(settings.data_dir), exchange=config.trading.exchange)
    bars = list(repo.load_bars(candidate.symbol, candidate.timeframe))
    if not bars:
        raise SystemExit(
            f"No {candidate.timeframe} bars — run: uv run python scripts/backfill_ig_external.py "
            f"--yahoo '{candidate.yahoo}' --epic {candidate.symbol} "
            f"--timeframe {candidate.timeframe}"
        )

    rules = InstrumentRulesCache(Path(settings.data_dir), max_age_hours=24 * 30).load(
        config.trading.exchange, candidate.symbol
    )
    if rules is None:
        raise SystemExit(
            f"No instrument rules for {candidate.symbol} — "
            f"`uv run trading-platform download-data --overlay {candidate.overlay} --days 1`"
        )

    span_days = (bars[-1].timestamp - bars[0].timestamp) / timedelta(days=1)
    print(
        f"\n########## {candidate.key.upper()} ##########\n"
        f"Validating {candidate.label} on {candidate.symbol}@{candidate.timeframe}: "
        f"{len(bars)} bars "
        f"({bars[0].timestamp.date()} -> {bars[-1].timestamp.date()}, ~{span_days:.0f}d)"
    )
    print(
        f"(spread_bps={config.backtest.spread_bps}, "
        f"starting_cash={config.backtest.starting_cash}; "
        "overnight funding not modelled)\n"
    )

    run = build_backtest_engine(
        container,
        rules,
        symbol=candidate.symbol,
        timeframe=candidate.timeframe,
        strategy_params=candidate.params,
    )
    try:
        full_result = run.engine.run(bars, candidate.timeframe)
    finally:
        run.teardown()
    full_report = build_performance_report(
        full_result,
        bars,
        min_round_trips=config.analytics.min_round_trips,
        min_bars=config.analytics.min_bars,
        min_daily_returns_for_sharpe=config.analytics.min_daily_returns_for_sharpe,
        bootstrap_iterations=config.analytics.bootstrap_iterations,
        bootstrap_seed=config.analytics.bootstrap_seed,
        market_sma_period=config.analytics.market_sma_period,
    )
    _print_metrics("Full history", full_report, bh=full_report.buy_and_hold_return_pct)

    trips = reconstruct_round_trips(full_result.fills)
    if trips:
        hours = [(t.exit_time - t.entry_time).total_seconds() / 3600 for t in trips]
        overnightish = sum(1 for h in hours if h >= 12)
        print(
            f"Hold stats: n={len(hours)}  median={sorted(hours)[len(hours) // 2]:.1f}h  "
            f"mean={sum(hours) / len(hours):.1f}h  >=12h={overnightish}/{len(hours)}"
        )
        print()

    windows = iter_walk_forward_windows(
        len(bars),
        is_bars=candidate.is_bars,
        oos_bars=candidate.oos_bars,
        step_bars=candidate.step_bars,
    )
    print(
        f"Rolling OOS (fixed params): {len(windows)} folds  "
        f"IS_spacer={candidate.is_bars} OOS={candidate.oos_bars} step={candidate.step_bars}"
    )
    print(f"\n{'fold':>4} {'OOS window':>24} {'oos_ret%':>9} {'oos_trips':>9} {'bh%':>8}")
    print("-" * 60)

    oos_results = []
    oos_returns: list[float] = []
    for fold_index, (_is_s, _is_e, oos_s, oos_e) in enumerate(windows):
        oos_bars = bars[oos_s:oos_e]
        run = build_backtest_engine(
            container,
            rules,
            symbol=candidate.symbol,
            timeframe=candidate.timeframe,
            strategy_params=candidate.params,
        )
        try:
            oos_result = run.engine.run(oos_bars, candidate.timeframe)
        finally:
            run.teardown()
        oos_results.append(oos_result)
        report = build_performance_report(
            oos_result,
            oos_bars,
            min_round_trips=1,
            min_bars=1,
            min_daily_returns_for_sharpe=1,
            bootstrap_iterations=1,
            bootstrap_seed=config.analytics.bootstrap_seed,
            market_sma_period=config.analytics.market_sma_period,
        )
        ret = float(report.metrics.total_return_pct)
        oos_returns.append(ret)
        bh = (
            float(report.buy_and_hold_return_pct)
            if report.buy_and_hold_return_pct is not None
            else float("nan")
        )
        a = oos_bars[0].timestamp.date()
        b = oos_bars[-1].timestamp.date()
        print(
            f"{fold_index:>4} {str(a)}->{b}  {ret:>+9.2f} "
            f"{report.metrics.round_trip_count:>9} {bh:>+8.2f}",
            flush=True,
        )

    stitched_ret: float | None = None
    oos_summary = "OOS folds: n/a"
    if oos_returns:
        wins = sum(1 for r in oos_returns if r > 0)
        oos_summary = (
            f"OOS folds: {len(oos_returns)}  positive={wins}/{len(oos_returns)}  "
            f"mean={sum(oos_returns) / len(oos_returns):+.2f}%  "
            f"median={sorted(oos_returns)[len(oos_returns) // 2]:+.2f}%"
        )
        print(f"\n{oos_summary}")
    stitched = stitch_oos_equity(oos_results, starting_cash=config.backtest.starting_cash)
    if stitched:
        start_eq = stitched[0].equity
        end_eq = stitched[-1].equity
        stitched_ret = float((end_eq - start_eq) / start_eq * 100) if start_eq else 0.0
        print(f"Stitched OOS equity return: {stitched_ret:+.2f}%")

    print(
        f"\nVerdict rule: do not promote to {candidate.overlay}.yaml unless OOS is "
        "consistently positive and preferably competitive with buy-and-hold."
    )

    m = full_report.metrics
    bh = full_report.buy_and_hold_return_pct
    bh_s = f"bh={float(bh):+.2f}%" if bh is not None else "bh=n/a"
    vs = f" vs_bh={float(m.total_return_pct) - float(bh):+.2f}%" if bh is not None else ""
    summary_lines = [
        f"Validation done: fx_mean_rev {candidate.key} {candidate.symbol}@{candidate.timeframe}",
        f"label={candidate.label}",
        (
            f"full: ret={float(m.total_return_pct):+.2f}% "
            f"maxdd={float(m.max_drawdown_pct):.2f}% trips={m.round_trip_count} "
            f"{bh_s}{vs}"
        ),
        oos_summary,
    ]
    if stitched_ret is not None:
        summary_lines.append(f"stitched OOS: {stitched_ret:+.2f}%")
    summary = "\n".join(summary_lines)
    if notify_demo_research(summary, enabled=discord):
        print("Posted summary to Discord demo webhook.", flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        choices=("eurusd", "eurchf"),
        default=None,
        help="Validate a single candidate (default: both).",
    )
    parser.add_argument(
        "--no-discord",
        action="store_true",
        help="Skip posting end-of-run summaries to DISCORD_DEMO_WEBHOOK_URL.",
    )
    args = parser.parse_args()

    chosen = [c for c in _CANDIDATES if args.only is None or c.key == args.only]
    if not chosen:
        raise SystemExit("No candidates selected.")

    for candidate in chosen:
        _validate_one(candidate, discord=not args.no_discord)


if __name__ == "__main__":
    main()
