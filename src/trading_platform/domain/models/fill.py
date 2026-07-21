from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from trading_platform.domain.models.order import OrderSide


class FeeType(StrEnum):
    MAKER = "maker"
    TAKER = "taker"


@dataclass(frozen=True, slots=True)
class Fill:
    """A (possibly partial) execution of an Order.

    Large orders may fill across multiple bars/ticks; `remaining_qty` and
    `is_complete` let PortfolioHandler and AnalyticsHandler accumulate correctly.
    """

    order_id: str
    correlation_id: str
    symbol: str
    side: OrderSide
    filled_qty: Decimal
    remaining_qty: Decimal
    fill_price: Decimal
    fee: Decimal
    fee_type: FeeType
    is_complete: bool
    timestamp: datetime
