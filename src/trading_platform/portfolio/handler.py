from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal

from trading_platform.domain.events.base import Event
from trading_platform.domain.events.execution import FillReceived
from trading_platform.domain.events.market import BarClosed
from trading_platform.domain.models.position import Position
from trading_platform.portfolio.book import PortfolioBook
from trading_platform.portfolio.persistence import (
    JsonPaperStateStore,
    snapshot_from_book,
)

logger = logging.getLogger(__name__)


class PortfolioHandler:
    """Applies fills to the paper/live portfolio book and persists state.

    Critical-path adjacent: exceptions are logged and re-raised so a failed
    apply does not silently desync risk sizing from reality.
    """

    name = "portfolio"

    def __init__(
        self,
        book: PortfolioBook,
        store: JsonPaperStateStore | None = None,
        *,
        last_bar_timestamp: datetime | None = None,
    ) -> None:
        self._book = book
        self._store = store
        self._last_bar_timestamp = last_bar_timestamp
        self._mark_prices: dict[str, Decimal] = {}

    @property
    def book(self) -> PortfolioBook:
        return self._book

    @property
    def last_bar_timestamp(self) -> datetime | None:
        return self._last_bar_timestamp

    @property
    def cash(self) -> Decimal:
        return self._book.cash

    def position_for(self, symbol: str) -> Position | None:
        return self._book.position_for(symbol)

    def equity(self, mark_prices: Mapping[str, Decimal]) -> Decimal:
        return self._book.equity(mark_prices)

    def handle(self, event: Event) -> None:
        if isinstance(event, FillReceived):
            self._on_fill(event)
        elif isinstance(event, BarClosed):
            self._on_bar(event)
        else:
            logger.debug(
                "portfolio_handler_ignored_event",
                extra={"event_type": type(event).__name__},
            )

    def _on_fill(self, event: FillReceived) -> None:
        self._book.apply_fill(event.fill)
        self._persist()
        logger.info(
            "portfolio_fill_applied",
            extra={
                "symbol": event.fill.symbol,
                "side": event.fill.side.value,
                "qty": str(event.fill.filled_qty),
                "price": str(event.fill.fill_price),
                "cash": str(self._book.cash),
            },
        )

    def _on_bar(self, event: BarClosed) -> None:
        bar = event.bar
        self._mark_prices[bar.symbol] = bar.close
        self._last_bar_timestamp = bar.timestamp
        self._persist()

    def _persist(self) -> None:
        if self._store is None:
            return
        self._store.save(
            snapshot_from_book(self._book, last_bar_timestamp=self._last_bar_timestamp)
        )
