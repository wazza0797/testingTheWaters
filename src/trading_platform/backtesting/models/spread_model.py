from __future__ import annotations

from decimal import Decimal

from trading_platform.domain.models.order import OrderSide

_BPS_DIVISOR = Decimal("10000")
_TWO = Decimal("2")


class SpreadModel:
    """Models bid/ask spread around a mid price — OHLCV bars have no real
    bid/ask, so this is a deliberate approximation (see the "Limitations"
    note in `docs/architecture.md`).

    A `BUY` fills at `mid + half_spread` (you cross the ask); a `SELL` fills
    at `mid - half_spread` (you cross the bid) — always worse than the mid,
    same as a real market order.
    """

    def __init__(self, spread_bps: float) -> None:
        if spread_bps < 0:
            raise ValueError(f"spread_bps must be non-negative, got {spread_bps}")
        self._half_spread_fraction = Decimal(str(spread_bps)) / _BPS_DIVISOR / _TWO

    def fill_price(self, side: OrderSide, mid_price: Decimal) -> Decimal:
        half_spread = mid_price * self._half_spread_fraction
        return mid_price + half_spread if side == OrderSide.BUY else mid_price - half_spread
