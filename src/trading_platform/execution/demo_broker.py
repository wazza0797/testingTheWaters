from __future__ import annotations

import logging
from decimal import Decimal

from trading_platform.domain.errors import ExchangeAdapterError
from trading_platform.domain.models.exchange_order import ExchangeOrderState, ExchangeOrderStatus
from trading_platform.domain.models.fill import FeeType, Fill
from trading_platform.domain.models.order import Order
from trading_platform.domain.ports.exchange import IExchangeAdapter

logger = logging.getLogger(__name__)


class DemoBroker:
    """Exchange-agnostic demo/practice `IBroker` over `IExchangeAdapter`.

    Places orders on whatever sandbox the adapter was constructed for (Binance
    Demo, Trading 212 practice, …). Does **not** use `FillSimulator` — fills
    come from `fetch_order` polling. Application code never sees venue SDKs.
    """

    def __init__(self, adapter: IExchangeAdapter) -> None:
        self._adapter = adapter
        # exchange_order_id → (client Order, qty reported, notional reported)
        self._open: dict[str, tuple[Order, Decimal, Decimal]] = {}

    def submit_order(self, order: Order) -> list[Fill]:
        """Submit to the venue; fills arrive asynchronously via `poll_fills`."""
        try:
            exchange_order_id = self._adapter.place_order(order)
        except ExchangeAdapterError:
            raise
        except NotImplementedError as exc:
            raise ExchangeAdapterError(
                f"{self._adapter.exchange_name} adapter cannot place demo orders yet: {exc}"
            ) from exc
        self._open[exchange_order_id] = (order, Decimal("0"), Decimal("0"))
        logger.info(
            "demo_order_submitted",
            extra={
                "exchange": self._adapter.exchange_name,
                "exchange_order_id": exchange_order_id,
                "symbol": order.symbol,
                "side": order.side.value,
            },
        )
        return []

    def poll_fills(self) -> list[tuple[Order, Fill]]:
        """Poll open orders; return new fill slices since the last poll."""
        produced: list[tuple[Order, Fill]] = []
        completed: list[str] = []

        for exchange_order_id, (order, reported_qty, reported_notional) in list(self._open.items()):
            status = self._adapter.fetch_order(exchange_order_id, order.symbol)
            fill = self._fill_delta(
                order, exchange_order_id, reported_qty, reported_notional, status
            )
            if fill is not None:
                produced.append((order, fill))
                reported_qty = reported_qty + fill.filled_qty
                reported_notional = reported_notional + (fill.filled_qty * fill.fill_price)
                self._open[exchange_order_id] = (order, reported_qty, reported_notional)

            if status.state in {
                ExchangeOrderState.FILLED,
                ExchangeOrderState.CANCELLED,
                ExchangeOrderState.REJECTED,
            }:
                completed.append(exchange_order_id)

        for exchange_order_id in completed:
            self._open.pop(exchange_order_id, None)

        return produced

    def has_open_orders(self) -> bool:
        return bool(self._open)

    def has_pending_order(self, symbol: str) -> bool:
        """Risk engine gate — true while any open demo order exists for `symbol`."""
        return any(order.symbol == symbol for order, _qty, _notional in self._open.values())

    def _fill_delta(
        self,
        order: Order,
        exchange_order_id: str,
        already_reported_qty: Decimal,
        already_reported_notional: Decimal,
        status: ExchangeOrderStatus,
    ) -> Fill | None:
        new_qty = status.filled_quantity - already_reported_qty
        if new_qty <= 0:
            return None
        if status.average_fill_price is None:
            raise ExchangeAdapterError(
                f"{self._adapter.exchange_name} order {exchange_order_id} "
                f"has fills but no average_fill_price"
            )
        # Infer this slice's price from cumulative VWAP so partial polls don't
        # reuse the all-time average as if it were the new tranche's price.
        cumulative_notional = status.average_fill_price * status.filled_quantity
        slice_notional = cumulative_notional - already_reported_notional
        fill_price = (slice_notional / new_qty).quantize(Decimal("0.00000001"))
        remaining = status.remaining_quantity
        is_complete = status.state == ExchangeOrderState.FILLED or remaining <= 0
        fee = Decimal("0")
        if status.filled_quantity > 0 and status.fee > 0:
            fee = (status.fee * new_qty / status.filled_quantity).quantize(Decimal("0.00000001"))
        return Fill(
            order_id=order.order_id,
            correlation_id=order.correlation_id,
            symbol=order.symbol,
            side=order.side,
            filled_qty=new_qty,
            remaining_qty=remaining,
            fill_price=fill_price,
            fee=fee,
            fee_type=FeeType.TAKER,
            is_complete=is_complete,
            timestamp=status.timestamp,
        )
