from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from trading_platform.domain.models.order import OrderSide, OrderType


class ExchangeOrderState(StrEnum):
    """Venue-neutral order lifecycle. Adapters map exchange-native statuses here."""

    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ExchangeOrderStatus:
    """Snapshot of an exchange order for demo/live fill polling.

    Produced only by `IExchangeAdapter.fetch_order` implementations — never by
    strategy/risk/application code constructing venue-specific payloads.
    """

    exchange_order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    state: ExchangeOrderState
    quantity: Decimal
    filled_quantity: Decimal
    remaining_quantity: Decimal
    average_fill_price: Decimal | None
    fee: Decimal
    fee_currency: str | None
    timestamp: datetime
