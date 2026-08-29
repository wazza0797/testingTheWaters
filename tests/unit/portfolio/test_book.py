from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from trading_platform.domain.errors import PortfolioError
from trading_platform.domain.models.fill import FeeType, Fill
from trading_platform.domain.models.order import OrderSide
from trading_platform.portfolio.book import PortfolioBook
from trading_platform.portfolio.persistence import (
    JsonPaperStateStore,
    book_from_snapshot,
    snapshot_from_book,
)

UTC_TS = datetime(2024, 1, 1, tzinfo=UTC)


def _fill(
    side: OrderSide,
    qty: str,
    price: str,
    fee: str = "0",
    *,
    day: int = 0,
) -> Fill:
    return Fill(
        order_id="o1",
        correlation_id="c1",
        symbol="BTC/USDT",
        side=side,
        filled_qty=Decimal(qty),
        remaining_qty=Decimal("0"),
        fill_price=Decimal(price),
        fee=Decimal(fee),
        fee_type=FeeType.TAKER,
        is_complete=True,
        timestamp=UTC_TS + timedelta(days=day),
    )


class TestPortfolioBook:
    def test_buy_then_sell_updates_cash(self) -> None:
        book = PortfolioBook(Decimal("10000"))
        book.apply_fill(_fill(OrderSide.BUY, "0.1", "50000", fee="5"))
        assert book.cash == Decimal("10000") - Decimal("5000") - Decimal("5")
        book.apply_fill(_fill(OrderSide.SELL, "0.1", "51000", fee="5", day=1))
        assert book.position_for("BTC/USDT") is None
        assert book.cash == Decimal("10000") - Decimal("5") - Decimal("5") + Decimal("100")

    def test_oversell_raises(self) -> None:
        book = PortfolioBook(Decimal("10000"))
        book.apply_fill(_fill(OrderSide.BUY, "0.1", "50000"))
        with pytest.raises(PortfolioError):
            book.apply_fill(_fill(OrderSide.SELL, "0.2", "50000", day=1))


class TestJsonPaperStateStore:
    def test_round_trip(self, tmp_path: Path) -> None:
        book = PortfolioBook(Decimal("10000"))
        book.apply_fill(_fill(OrderSide.BUY, "0.1", "50000", fee="1"))
        store = JsonPaperStateStore(tmp_path / "paper_state.json")
        store.save(snapshot_from_book(book, last_bar_timestamp=UTC_TS))

        loaded = store.load()
        assert loaded is not None
        restored = book_from_snapshot(loaded)
        assert restored.cash == book.cash
        assert restored.position_for("BTC/USDT") is not None
        assert restored.position_for("BTC/USDT").quantity == Decimal("0.1")
        assert loaded.last_bar_timestamp == UTC_TS
        assert len(restored.fills) == 1
