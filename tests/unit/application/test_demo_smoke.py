from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_platform.application.demo_smoke import run_demo_smoke
from trading_platform.domain.errors import ExchangeAdapterError, TradingPlatformError
from trading_platform.domain.models.exchange_order import ExchangeOrderState, ExchangeOrderStatus
from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.domain.models.order import Order, OrderSide, OrderType


class _FakeDemoAdapter:
    exchange_name = "fake-demo"

    def __init__(
        self,
        *,
        allows_short: bool = False,
        existing_qty: Decimal = Decimal("0"),
        reject_open: bool = False,
    ) -> None:
        self._allows_short = allows_short
        self._existing_qty = existing_qty
        self._reject_open = reject_open
        self._orders: dict[str, ExchangeOrderStatus] = {}
        self.placed: list[Order] = []
        self._seq = 0

    def fetch_ohlcv(self, symbol: str, timeframe: str, since=None, limit=None):  # noqa: ANN001
        return []

    def fetch_instrument_rules(self, symbol: str) -> InstrumentRules:
        return InstrumentRules(
            exchange=self.exchange_name,
            symbol=symbol,
            tick_size=Decimal("0.01"),
            step_size=Decimal("0.1"),
            min_qty=Decimal("0.1"),
            min_notional=Decimal("0"),
            price_precision=2,
            qty_precision=1,
            maker_fee_rate=Decimal("0"),
            taker_fee_rate=Decimal("0"),
            allows_short=self._allows_short,
        )

    def place_order(self, order: Order) -> str:
        self.placed.append(order)
        self._seq += 1
        deal_id = f"deal-{self._seq}"
        state = (
            ExchangeOrderState.REJECTED
            if self._reject_open and len(self.placed) == 1
            else ExchangeOrderState.FILLED
        )
        self._orders[deal_id] = ExchangeOrderStatus(
            exchange_order_id=deal_id,
            symbol=order.symbol,
            side=order.side,
            order_type=OrderType.MARKET,
            state=state,
            quantity=order.quantity,
            filled_quantity=order.quantity if state is ExchangeOrderState.FILLED else Decimal("0"),
            remaining_quantity=Decimal("0")
            if state is ExchangeOrderState.FILLED
            else order.quantity,
            average_fill_price=Decimal("100") if state is ExchangeOrderState.FILLED else None,
            fee=Decimal("0"),
            fee_currency=None,
            timestamp=datetime.now(tz=UTC),
        )
        return deal_id

    def cancel_order(self, order_id: str, symbol: str) -> None:
        return None

    def get_balance(self, asset: str) -> Decimal:
        if asset in {"ACCOUNT", "USDT", "GBP"}:
            return Decimal("10000")
        if asset in {"BTC", "CS.D.EURUSD.MINI.IP"}:
            return self._existing_qty
        return Decimal("0")

    def fetch_order(self, order_id: str, symbol: str) -> ExchangeOrderStatus:
        return self._orders[order_id]


class TestDemoSmoke:
    def test_open_and_close_long(self) -> None:
        adapter = _FakeDemoAdapter()
        result = run_demo_smoke(adapter, "BTC/USDT", poll_interval_sec=0)
        assert result.open_status.state is ExchangeOrderState.FILLED
        assert result.close_status is not None
        assert result.close_status.state is ExchangeOrderState.FILLED
        assert [o.side for o in adapter.placed] == [OrderSide.BUY, OrderSide.SELL]
        assert result.quantity == Decimal("0.1")

    def test_refuses_when_already_in_position(self) -> None:
        adapter = _FakeDemoAdapter(existing_qty=Decimal("1"))
        with pytest.raises(TradingPlatformError, match="existing position"):
            run_demo_smoke(adapter, "BTC/USDT", poll_interval_sec=0)

    def test_short_requires_allows_short(self) -> None:
        adapter = _FakeDemoAdapter(allows_short=False)
        with pytest.raises(TradingPlatformError, match="does not allow short"):
            run_demo_smoke(adapter, "BTC/USDT", side=OrderSide.SELL, poll_interval_sec=0)

    def test_short_open_and_cover_when_allowed(self) -> None:
        adapter = _FakeDemoAdapter(allows_short=True)
        result = run_demo_smoke(
            adapter, "CS.D.EURUSD.MINI.IP", side=OrderSide.SELL, poll_interval_sec=0
        )
        assert [o.side for o in adapter.placed] == [OrderSide.SELL, OrderSide.BUY]
        assert result.close_status is not None

    def test_rejected_open_raises(self) -> None:
        adapter = _FakeDemoAdapter(reject_open=True)
        with pytest.raises(ExchangeAdapterError, match="rejected"):
            run_demo_smoke(adapter, "BTC/USDT", poll_interval_sec=0)

    def test_rejected_open_includes_venue_message(self) -> None:
        adapter = _FakeDemoAdapter(reject_open=True)
        # Inject venue reason onto the stored reject status after place.
        original_place = adapter.place_order

        def place_with_reason(order: Order) -> str:
            deal_id = original_place(order)
            old = adapter._orders[deal_id]
            adapter._orders[deal_id] = ExchangeOrderStatus(
                exchange_order_id=old.exchange_order_id,
                symbol=old.symbol,
                side=old.side,
                order_type=old.order_type,
                state=old.state,
                quantity=old.quantity,
                filled_quantity=old.filled_quantity,
                remaining_quantity=old.remaining_quantity,
                average_fill_price=old.average_fill_price,
                fee=old.fee,
                fee_currency=old.fee_currency,
                timestamp=old.timestamp,
                venue_message="MARKET_CLOSED",
            )
            return deal_id

        adapter.place_order = place_with_reason  # type: ignore[method-assign]
        with pytest.raises(ExchangeAdapterError, match="MARKET_CLOSED"):
            run_demo_smoke(adapter, "BTC/USDT", poll_interval_sec=0)

    def test_no_close_leaves_position(self) -> None:
        adapter = _FakeDemoAdapter()
        result = run_demo_smoke(adapter, "BTC/USDT", close=False, poll_interval_sec=0)
        assert len(adapter.placed) == 1
        assert result.close_status is None
