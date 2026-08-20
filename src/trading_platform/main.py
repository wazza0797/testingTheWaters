from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import timedelta

import typer
import uvicorn
import yaml

from trading_platform import __version__
from trading_platform.config.loader import load_config
from trading_platform.config.settings import Settings
from trading_platform.container import AppContainer, build_container
from trading_platform.domain.errors import TradingPlatformError
from trading_platform.domain.events.system import Heartbeat
from trading_platform.utils.logging import configure_logging
from trading_platform.utils.time import utc_now

_HEARTBEAT_INTERVAL_SECONDS = 10.0

app = typer.Typer(
    name="trading-platform",
    help="Modular algorithmic trading platform — crypto-first, exchange-agnostic.",
    no_args_is_help=True,
)

logger = logging.getLogger(__name__)


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
    except TradingPlatformError as exc:
        typer.echo(f"Download failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def backtest() -> None:
    """Run a strategy backtest. Implemented in Milestone 4."""
    typer.echo("Not yet implemented — see Milestone 4 (Backtesting Engine).")
    raise typer.Exit(code=1)


@app.command()
def paper() -> None:
    """Run the paper trading loop. Implemented in Milestone 6."""
    typer.echo("Not yet implemented — see Milestone 6 (Paper Trading).")
    raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Print the installed trading-platform version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
