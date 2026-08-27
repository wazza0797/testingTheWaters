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

    Optional volatility scaling (Milestone 4.5 Phase B): when
    `volatility_k > 0` and a current ATR is supplied, half-spread widens by
    `k * (ATR / mid)` so fills are more expensive in volatile regimes. With
    `volatility_k == 0` (the default), behaviour is identical to the flat
    `spread_bps` model from Milestone 4.
    """

    def __init__(
        self,
        spread_bps: float,
        *,
        volatility_k: float = 0.0,
        atr_period: int = 14,
    ) -> None:
        if spread_bps < 0:
            raise ValueError(f"spread_bps must be non-negative, got {spread_bps}")
        if volatility_k < 0:
            raise ValueError(f"volatility_k must be non-negative, got {volatility_k}")
        if atr_period < 1:
            raise ValueError(f"atr_period must be >= 1, got {atr_period}")
        self._half_spread_fraction = Decimal(str(spread_bps)) / _BPS_DIVISOR / _TWO
        self._volatility_k = Decimal(str(volatility_k))
        self._atr_period = atr_period

    @property
    def atr_period(self) -> int:
        return self._atr_period

    @property
    def volatility_enabled(self) -> bool:
        return self._volatility_k > 0

    def fill_price(
        self,
        side: OrderSide,
        mid_price: Decimal,
        *,
        atr: Decimal | None = None,
    ) -> Decimal:
        half_fraction = self._half_spread_fraction
        if self._volatility_k > 0 and atr is not None and mid_price > 0:
            half_fraction = half_fraction + self._volatility_k * (atr / mid_price)
        half_spread = mid_price * half_fraction
        return mid_price + half_spread if side == OrderSide.BUY else mid_price - half_spread
