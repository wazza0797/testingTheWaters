from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from trading_platform.domain.models.position import Position


@dataclass(frozen=True, slots=True)
class Portfolio:
    """A point-in-time snapshot of cash and positions, keyed by symbol."""

    cash: Decimal
    positions: Mapping[str, Position] = field(default_factory=dict)
    timestamp: datetime | None = None

    def position_for(self, symbol: str) -> Position | None:
        return self.positions.get(symbol)

    def equity(self, mark_prices: Mapping[str, Decimal]) -> Decimal:
        """Total account value: cash + mark-to-market value of all positions."""
        total = self.cash
        for symbol, position in self.positions.items():
            price = mark_prices.get(symbol)
            if price is not None:
                total += position.quantity * price
        return total
