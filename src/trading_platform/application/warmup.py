"""Load historical bars to warm strategies before paper/demo live polling."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from trading_platform.domain.models.bar import Bar
from trading_platform.domain.ports.exchange import IExchangeAdapter
from trading_platform.domain.ports.market_data import IMarketDataRepository

logger = logging.getLogger(__name__)


def resolve_warmup_bar_count(strategy_params: Mapping[str, Any]) -> int:
    """Bars needed before Connors / SMA-regime strategies can signal."""

    def _int(key: str, default: int = 0) -> int:
        raw = strategy_params.get(key, default)
        if isinstance(raw, bool):
            return default
        if isinstance(raw, int):
            return raw
        if isinstance(raw, float):
            return int(raw)
        if isinstance(raw, str):
            try:
                return int(raw)
            except ValueError:
                return default
        return default

    sma_regime = _int("sma_regime_period")
    lookback = _int("lookback")
    slow = _int("slow_period")
    atr = _int("atr_period")
    need = max(sma_regime, lookback, slow, atr, 50) + 20
    return max(need, 70)


def load_warmup_bars(
    repository: IMarketDataRepository,
    adapter: IExchangeAdapter,
    symbol: str,
    timeframe: str,
    *,
    min_bars: int,
    through: datetime | None = None,
) -> list[Bar]:
    """Prefer local parquet cache; fall back to a single venue OHLCV fetch.

    When `through` is set (resume cursor), only bars at or before that
    timestamp are returned. Otherwise the newest `min_bars` closed bars
    are returned so the live loop can advance the cursor past them.
    """
    cached = list(repository.load_bars(symbol, timeframe))
    bars = cached
    if len(bars) < min_bars:
        limit = min(adapter.max_ohlcv_limit, max(min_bars, 250))
        try:
            fetched = adapter.fetch_ohlcv(symbol, timeframe, limit=limit)
        except Exception:
            logger.exception(
                "warmup_fetch_ohlcv_failed",
                extra={"symbol": symbol, "timeframe": timeframe},
            )
            fetched = []
        if len(fetched) > len(bars):
            bars = fetched
            logger.info(
                "warmup_bars_from_venue",
                extra={"symbol": symbol, "bars": len(bars)},
            )
        elif bars:
            logger.warning(
                "warmup_bars_short",
                extra={
                    "symbol": symbol,
                    "cached": len(cached),
                    "fetched": len(fetched),
                    "needed": min_bars,
                },
            )

    if through is not None:
        bars = [b for b in bars if b.timestamp <= through]
    elif len(bars) > min_bars:
        bars = bars[-min_bars:]

    return bars
