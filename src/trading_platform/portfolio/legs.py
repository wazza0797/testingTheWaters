"""Per-symbol core vs tilt leg quantities (internal accounting).

IG nets both legs into one CFD position; this book is the runner's attribution
layer so the daily tilt loop can open/close without disturbing a static core.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

LegName = Literal["core", "tilt"]


@dataclass
class SymbolLegs:
    core: Decimal = field(default_factory=lambda: Decimal("0"))
    tilt: Decimal = field(default_factory=lambda: Decimal("0"))

    def qty(self, leg: LegName) -> Decimal:
        return self.core if leg == "core" else self.tilt

    @property
    def net(self) -> Decimal:
        return self.core + self.tilt


class LegBook:
    """Mutable core/tilt quantities keyed by exchange symbol."""

    def __init__(self) -> None:
        self._legs: dict[str, SymbolLegs] = {}

    def qty(self, symbol: str, leg: LegName) -> Decimal:
        row = self._legs.get(symbol)
        if row is None:
            return Decimal("0")
        return row.qty(leg)

    def net(self, symbol: str) -> Decimal:
        row = self._legs.get(symbol)
        return row.net if row is not None else Decimal("0")

    def set_qty(self, symbol: str, leg: LegName, quantity: Decimal) -> None:
        if quantity < 0:
            raise ValueError(f"leg quantity must be >= 0, got {quantity}")
        row = self._legs.setdefault(symbol, SymbolLegs())
        if leg == "core":
            row.core = quantity
        else:
            row.tilt = quantity
        if row.core == 0 and row.tilt == 0:
            self._legs.pop(symbol, None)

    def add(self, symbol: str, leg: LegName, delta: Decimal) -> None:
        current = self.qty(symbol, leg)
        self.set_qty(symbol, leg, current + delta)

    def apply_fill(
        self,
        symbol: str,
        *,
        leg: LegName,
        side: str,
        filled_qty: Decimal,
    ) -> None:
        """Attribute a fill to a leg. `side` is 'buy' | 'sell'."""
        if filled_qty <= 0:
            return
        if side == "buy":
            self.add(symbol, leg, filled_qty)
            return
        if side == "sell":
            current = self.qty(symbol, leg)
            if filled_qty > current:
                # Clamp — net book is source of truth for exchange; attribution
                # should not go negative if a reconcile races.
                self.set_qty(symbol, leg, Decimal("0"))
            else:
                self.set_qty(symbol, leg, current - filled_qty)
            return
        raise ValueError(f"unsupported fill side {side!r}")

    def snapshot(self) -> dict[str, dict[str, str]]:
        return {
            symbol: {"core": str(row.core), "tilt": str(row.tilt)}
            for symbol, row in sorted(self._legs.items())
        }


class LegAttributionHandler:
    """Event-bus side effect: attribute fills to the LegBook from order metadata.

    Used in backtest (where `PortfolioHandler` is not on the bus) and can
    co-exist with `PortfolioHandler`'s own leg updates in paper/demo — callers
    should attach **either** this handler **or** pass `leg_book` into
    `PortfolioHandler`, not both, to avoid double-counting.
    """

    name = "leg_attribution"

    def __init__(self, leg_book: LegBook) -> None:
        self._leg_book = leg_book

    def handle(self, event: object) -> None:
        from trading_platform.domain.events.execution import FillReceived

        if not isinstance(event, FillReceived):
            return
        raw_leg = event.order.metadata.get("leg")
        if raw_leg == "core" or raw_leg == "tilt":
            self._leg_book.apply_fill(
                event.fill.symbol,
                leg=raw_leg,
                side=event.fill.side.value,
                filled_qty=event.fill.filled_qty,
            )
