from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_platform.domain.models.bar import Bar
from trading_platform.portfolio.seed import seed_book_from_exchange


class FakeBalanceAdapter:
    exchange_name = "fake"

    def __init__(self, *, usdt: str = "5000", btc: str = "0.1") -> None:
        self._usdt = Decimal(usdt)
        self._btc = Decimal(btc)

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
        return [
            Bar(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=datetime(2024, 1, 1, tzinfo=UTC),
                open=Decimal("100"),
                high=Decimal("100"),
                low=Decimal("100"),
                close=Decimal("100"),
                volume=Decimal("1"),
            )
        ]

    def fetch_instrument_rules(self, symbol):  # pragma: no cover
        raise NotImplementedError

    def place_order(self, order):  # pragma: no cover
        raise NotImplementedError

    def cancel_order(self, order_id, symbol):  # pragma: no cover
        raise NotImplementedError

    def get_balance(self, asset: str) -> Decimal:
        if asset == "USDT":
            return self._usdt
        if asset == "BTC":
            return self._btc
        return Decimal("0")

    def fetch_order(self, order_id, symbol):  # pragma: no cover
        raise NotImplementedError


class TestSeedBookFromExchange:
    def test_seeds_cash_and_base_position(self) -> None:
        book = seed_book_from_exchange(
            FakeBalanceAdapter(),  # type: ignore[arg-type]
            "BTC/USDT",
            timeframe="1h",
        )
        assert book.cash == Decimal("5000")
        pos = book.position_for("BTC/USDT")
        assert pos is not None
        assert pos.quantity == Decimal("0.1")
        assert pos.average_entry_price == Decimal("100")

    def test_flat_when_no_base(self) -> None:
        book = seed_book_from_exchange(
            FakeBalanceAdapter(btc="0"),  # type: ignore[arg-type]
            "BTC/USDT",
            timeframe="1h",
        )
        assert book.position_for("BTC/USDT") is None
