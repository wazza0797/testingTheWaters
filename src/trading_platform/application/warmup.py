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
    """Prefer local parquet; fetch+persist from the venue when history is short.

    When `through` is set (resume cursor), only bars at or before that
    timestamp are used for the strategy warm window — but insufficiency is
    measured *after* that filter so a redeploy with an advanced cursor still
    backfills enough SMA/ATR history. Venue bars are written to the repository
    so the next restart does not depend on a one-shot OHLCV call.
    """
    by_ts: dict[datetime, Bar] = {
        bar.timestamp: bar for bar in repository.load_bars(symbol, timeframe)
    }

    def _usable() -> list[Bar]:
        bars = sorted(by_ts.values(), key=lambda b: b.timestamp)
        if through is not None:
            bars = [b for b in bars if b.timestamp <= through]
        return bars

    if len(_usable()) < min_bars:
        limit = min(adapter.max_ohlcv_limit, max(min_bars, 250))
        try:
            fetched = adapter.fetch_ohlcv(symbol, timeframe, limit=limit)
        except Exception:
            logger.exception(
                "warmup_fetch_ohlcv_failed",
                extra={"symbol": symbol, "timeframe": timeframe},
            )
            fetched = []
        if fetched:
            repository.save_bars(symbol, timeframe, fetched)
            for bar in fetched:
                by_ts[bar.timestamp] = bar
            logger.info(
                "warmup_bars_from_venue",
                extra={"symbol": symbol, "fetched": len(fetched), "usable": len(_usable())},
            )

    bars = _usable()
    if len(bars) < min_bars:
        logger.warning(
            "warmup_bars_short",
            extra={
                "symbol": symbol,
                "have": len(bars),
                "needed": min_bars,
                "through": through.isoformat() if through is not None else None,
            },
        )
    if len(bars) > min_bars:
        bars = bars[-min_bars:]
    return bars
