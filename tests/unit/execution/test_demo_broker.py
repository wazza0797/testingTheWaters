from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_platform.domain.models.exchange_order import ExchangeOrderState, ExchangeOrderStatus
from trading_platform.domain.models.order import Order, OrderSide, OrderType
from trading_platform.execution.demo_broker import DemoBroker


def _order() -> Order:
    return Order(
        order_id="client-1",
        correlation_id="corr-1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.01"),
        price=None,
        strategy_name="test",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


class FakeDemoAdapter:
    exchange_name = "fake-demo"

    def __init__(self) -> None:
        self.placed: list[Order] = []
        self._statuses: dict[str, ExchangeOrderStatus] = {}
        self._next_id = 1

    def fetch_ohlcv(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError

    def fetch_instrument_rules(self, symbol: str):  # pragma: no cover
        raise NotImplementedError

    def place_order(self, order: Order) -> str:
        oid = f"ex-{self._next_id}"
        self._next_id += 1
        self.placed.append(order)
        self._statuses[oid] = ExchangeOrderStatus(
            exchange_order_id=oid,
            symbol=order.symbol,
            side=order.side,
            order_type=order.order_type,
            state=ExchangeOrderState.OPEN,
            quantity=order.quantity,
            filled_quantity=Decimal("0"),
            remaining_quantity=order.quantity,
            average_fill_price=None,
            fee=Decimal("0"),
            fee_currency=None,
            timestamp=order.created_at,
        )
        return oid

    def cancel_order(self, order_id: str, symbol: str) -> None:  # pragma: no cover
        raise NotImplementedError

    def get_balance(self, asset: str) -> Decimal:  # pragma: no cover
        raise NotImplementedError

    def fetch_order(self, order_id: str, symbol: str) -> ExchangeOrderStatus:
        return self._statuses[order_id]

    def complete(self, order_id: str, price: Decimal, fee: Decimal = Decimal("0.01")) -> None:
        prev = self._statuses[order_id]
        self._statuses[order_id] = ExchangeOrderStatus(
            exchange_order_id=order_id,
            symbol=prev.symbol,
            side=prev.side,
            order_type=prev.order_type,
            state=ExchangeOrderState.FILLED,
            quantity=prev.quantity,
            filled_quantity=prev.quantity,
            remaining_quantity=Decimal("0"),
            average_fill_price=price,
            fee=fee,
            fee_currency="USDT",
            timestamp=datetime(2024, 1, 1, 1, tzinfo=UTC),
        )


class TestDemoBroker:
    def test_submit_then_poll_emits_fill_delta(self) -> None:
        adapter = FakeDemoAdapter()
        broker = DemoBroker(adapter)  # type: ignore[arg-type]
        order = _order()

        assert broker.submit_order(order) == []
        assert broker.has_open_orders()
        assert adapter.placed == [order]

        assert broker.poll_fills() == []

        exchange_id = next(iter(adapter._statuses))
        adapter.complete(exchange_id, price=Decimal("100"))
        fills = broker.poll_fills()
        assert len(fills) == 1
        client_order, fill = fills[0]
        assert client_order is order
        assert fill.filled_qty == Decimal("0.01")
        assert fill.fill_price == Decimal("100")
        assert fill.is_complete is True
        assert not broker.has_open_orders()

    def test_partial_then_complete(self) -> None:
        adapter = FakeDemoAdapter()
        broker = DemoBroker(adapter)  # type: ignore[arg-type]
        order = _order()
        broker.submit_order(order)
        exchange_id = next(iter(adapter._statuses))

        prev = adapter._statuses[exchange_id]
        adapter._statuses[exchange_id] = ExchangeOrderStatus(
            exchange_order_id=exchange_id,
            symbol=prev.symbol,
            side=prev.side,
            order_type=prev.order_type,
            state=ExchangeOrderState.PARTIALLY_FILLED,
            quantity=prev.quantity,
            filled_quantity=Decimal("0.004"),
            remaining_quantity=Decimal("0.006"),
            average_fill_price=Decimal("99"),
            fee=Decimal("0.004"),
            fee_currency="USDT",
            timestamp=datetime(2024, 1, 1, 1, tzinfo=UTC),
        )
        first = broker.poll_fills()
        assert len(first) == 1
        assert first[0][1].filled_qty == Decimal("0.004")
        assert first[0][1].is_complete is False

        adapter.complete(exchange_id, price=Decimal("100"), fee=Decimal("0.01"))
        second = broker.poll_fills()
        assert len(second) == 1
        assert second[0][1].filled_qty == Decimal("0.006")
        assert second[0][1].is_complete is True
