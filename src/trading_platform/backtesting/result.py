from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from trading_platform.domain.models.fill import Fill
from trading_platform.domain.models.position import Position


@dataclass(frozen=True, slots=True)
class EquityPoint:
    """One point on the equity curve — total account value at a bar's close."""

    timestamp: datetime
    equity: Decimal


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """Everything a backtest run produced: the trade log (every fill,
    including partials) and the equity curve, plus enough summary fields to
    sanity-check a run without needing the full performance-metrics engine
    (Sharpe, drawdown, win rate — that's Milestone 5's `analytics/`, deliberately
    out of scope here). `EquityPoint`/`Fill` are the two building blocks M5
    will consume; this class intentionally does no further analysis of them.
    """

    symbol: str
    timeframe: str
    starting_cash: Decimal
    ending_cash: Decimal
    bars_processed: int
    fills: tuple[Fill, ...]
    total_fees_paid: Decimal
    equity_curve: tuple[EquityPoint, ...]
    final_position: Position | None

    @property
    def ending_equity(self) -> Decimal:
        if self.equity_curve:
            return self.equity_curve[-1].equity
        return self.ending_cash

    @property
    def total_return_pct(self) -> Decimal:
        if self.starting_cash == 0:
            return Decimal("0")
        return (self.ending_equity - self.starting_cash) / self.starting_cash * Decimal("100")
