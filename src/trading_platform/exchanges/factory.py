"""Build `IExchangeAdapter` instances from (exchange name, execution mode).

Adding a venue = new package under `exchanges/<name>/` + a branch here.
Application / demo / paper loops never construct adapters directly with
vendor-specific options.
"""

from __future__ import annotations

from trading_platform.config.settings import Environment, Settings
from trading_platform.domain.errors import ConfigurationError
from trading_platform.domain.ports.exchange import IExchangeAdapter
from trading_platform.exchanges.binance.adapter import BinanceAdapter
from trading_platform.exchanges.ig.adapter import IgAdapter


def build_exchange_adapter(
    exchange: str,
    mode: Environment,
    settings: Settings,
) -> IExchangeAdapter:
    """Composition-root helper: pick the concrete adapter for this run.

    - `paper` / `backtest`: public market-data adapter when the venue allows it
      (Binance); IG has no public OHLCV — uses demo credentials for history.
    - `demo`: sandbox/practice endpoints + demo API credentials
    - `live`: mainnet + live credentials (gated; not implemented for trading yet)
    """
    name = exchange.strip().lower()
    if name == "binance":
        return _build_binance(mode, settings)
    if name == "ig":
        return _build_ig(mode, settings)
    raise ConfigurationError(
        f"Unsupported exchange {exchange!r}. Known: 'binance', 'ig'. "
        "Add exchanges/<name>/ and a factory branch to support another venue."
    )


def _build_binance(mode: Environment, settings: Settings) -> IExchangeAdapter:
    if mode in (Environment.PAPER, Environment.BACKTEST):
        # Public OHLCV / rules — existing default construction.
        return BinanceAdapter()
    if mode == Environment.DEMO:
        return BinanceAdapter.for_demo(
            api_key=settings.binance_demo_api_key,
            api_secret=settings.binance_demo_api_secret,
        )
    if mode == Environment.LIVE:
        raise ConfigurationError(
            "Live Binance adapter is not implemented yet — use ENV=demo for "
            "exchange sandbox orders, or ENV=paper for local FillSimulator paper."
        )
    raise ConfigurationError(f"Unsupported environment for Binance adapter: {mode}")


def _build_ig(mode: Environment, settings: Settings) -> IExchangeAdapter:
    if mode == Environment.LIVE:
        # Scaffold exists (`IgAdapter.for_live`) but trading is not unlocked —
        # same barrier pattern as Binance live today.
        raise ConfigurationError(
            "Live IG adapter is not implemented yet — use ENV=demo with "
            "IG_DEMO_* credentials against demo-api.ig.com, or ENV=paper for "
            "local FillSimulator paper."
        )
    if mode in (Environment.DEMO, Environment.PAPER, Environment.BACKTEST):
        # IG has no public market-data API; demo credentials are required even
        # for history download / paper bar polls when exchange=ig.
        return IgAdapter.for_demo(
            api_key=settings.ig_demo_api_key,
            username=settings.ig_demo_username,
            password=settings.ig_demo_password,
            account_id=settings.ig_demo_account_id,
        )
    raise ConfigurationError(f"Unsupported environment for IG adapter: {mode}")
