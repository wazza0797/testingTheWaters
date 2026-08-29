from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal

from trading_platform.domain.models.fill import Fill
from trading_platform.domain.models.position import Position
from trading_platform.portfolio.book import PortfolioBook


class BacktestLedger:
    """In-memory cash/position bookkeeping for a single backtest run.

    Thin wrapper over `PortfolioBook` — backtest owns the book directly (no
    event bus, no disk). Milestone 6's `PortfolioHandler` uses the same book
    for paper trading with persistence. See `portfolio/book.py`.
    """

    def __init__(self, starting_cash: Decimal) -> None:
        self._book = PortfolioBook(starting_cash)

    def position_for(self, symbol: str) -> Position | None:
        return self._book.position_for(symbol)

    def equity(self, mark_prices: Mapping[str, Decimal]) -> Decimal:
        return self._book.equity(mark_prices)

    @property
    def cash(self) -> Decimal:
        return self._book.cash

    @property
    def timestamp(self) -> datetime | None:
        return self._book.timestamp

    @property
    def fills(self) -> tuple[Fill, ...]:
        return self._book.fills

    @property
    def total_fees_paid(self) -> Decimal:
        return self._book.total_fees_paid

    def apply_fill(self, fill: Fill) -> None:
        self._book.apply_fill(fill)
