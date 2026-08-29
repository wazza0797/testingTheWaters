from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from trading_platform.domain.events.execution import FillReceived
from trading_platform.domain.events.market import BarClosed
from trading_platform.domain.models.fill import FeeType, Fill
from trading_platform.domain.models.order import Order, OrderSide, OrderType
from trading_platform.portfolio.book import PortfolioBook
from trading_platform.portfolio.handler import PortfolioHandler
from trading_platform.portfolio.persistence import JsonPaperStateStore

UTC_TS = datetime(2024, 1, 1, tzinfo=UTC)


def _order() -> Order:
    return Order(
        order_id="o1",
        correlation_id="c1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.1"),
        price=None,
        strategy_name="test",
        created_at=UTC_TS,
    )


def _fill() -> Fill:
    return Fill(
        order_id="o1",
        correlation_id="c1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        filled_qty=Decimal("0.1"),
        remaining_qty=Decimal("0"),
        fill_price=Decimal("50000"),
        fee=Decimal("1"),
        fee_type=FeeType.TAKER,
        is_complete=True,
        timestamp=UTC_TS,
    )


class TestPortfolioHandler:
    def test_fill_received_applies_and_persists(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        store = JsonPaperStateStore(path)
        book = PortfolioBook(Decimal("10000"))
        handler = PortfolioHandler(book, store)

        handler.handle(FillReceived(fill=_fill(), order=_order()))

        assert book.position_for("BTC/USDT") is not None
        assert path.is_file()
        reloaded = store.load()
        assert reloaded is not None
        assert reloaded.cash == book.cash

    def test_bar_closed_updates_last_timestamp(self, make_bar, tmp_path: Path) -> None:
        handler = PortfolioHandler(
            PortfolioBook(Decimal("10000")),
            JsonPaperStateStore(tmp_path / "s.json"),
        )
        bar = make_bar(
            timestamp=UTC_TS + timedelta(hours=1),
            open_="51000",
            high="51000",
            low="51000",
            close="51000",
        )
        handler.handle(BarClosed(bar=bar, mode="paper"))
        assert handler.last_bar_timestamp == bar.timestamp
