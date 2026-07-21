from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from trading_platform.domain.events.base import Event
from trading_platform.domain.models.bar import Bar


@dataclass(frozen=True, slots=True, kw_only=True)
class BarClosed(Event):
    """Published by TradingLoop/BacktestEngine — the only event that drives time forward."""

    bar: Bar
    mode: str  # "backtest" | "paper" | "live"


@dataclass(frozen=True, slots=True, kw_only=True)
class FeedStalled(Event):
    """Published when a live/paper market data feed has not produced a bar recently."""

    symbol: str
    last_bar_timestamp: datetime | None
    stalled_seconds: float
