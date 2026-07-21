from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class PositionSide(StrEnum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


@dataclass(frozen=True, slots=True)
class Position:
    """A symbol's net position. `quantity` is signed: positive=long, negative=short."""

    symbol: str
    quantity: Decimal
    average_entry_price: Decimal
    realized_pnl: Decimal = Decimal("0")

    @property
    def side(self) -> PositionSide:
        if self.quantity > 0:
            return PositionSide.LONG
        if self.quantity < 0:
            return PositionSide.SHORT
        return PositionSide.FLAT

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0

    def unrealized_pnl(self, mark_price: Decimal) -> Decimal:
        return (mark_price - self.average_entry_price) * self.quantity
