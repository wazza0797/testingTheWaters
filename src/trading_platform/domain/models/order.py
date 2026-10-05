from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class Order:
    """An order approved by the risk engine, ready for the execution layer.

    `price` is `None` for market orders. Quantity/price are already rounded to
    the instrument's step/tick size by the time an Order is constructed.

    `metadata` carries strategy attribution (e.g. `leg`, `reason`) from the
    originating `Signal` so portfolio leg books and notifications stay
    attributable after the risk engine has sized the order.
    """

    order_id: str
    correlation_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    price: Decimal | None
    strategy_name: str
    created_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict)
