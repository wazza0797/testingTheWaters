from __future__ import annotations

from decimal import Decimal

from trading_platform.domain.models.order import OrderSide

_BPS_DIVISOR = Decimal("10000")
_TWO = Decimal("2")
# Cap ATR/mid used for vol scaling so a single wild bar can't push fill cost
# (and the matching cash-sufficiency guard) to absurd levels. 5% ATR/price is
# already extreme for BTC hourly; beyond that we treat the extra as unmodelled.
_MAX_ATR_OVER_PRICE = Decimal("0.05")


def half_spread_fraction_from_bps(spread_bps: float) -> Decimal:
    """Convert flat `spread_bps` into the half-spread as a fraction of mid."""
    return Decimal(str(spread_bps)) / _BPS_DIVISOR / _TWO


def max_half_spread_fraction(spread_bps: float, volatility_k: float = 0.0) -> Decimal:
    """Worst-case half-spread fraction used by `SpreadModel` *and* by
    `PassThroughRiskEngine`'s cash-sufficiency guard — must stay in sync so a
    vol-widened fill cannot overdraw the ledger after an order was approved.
    """
    base = half_spread_fraction_from_bps(spread_bps)
    if volatility_k <= 0:
        return base
    return base + Decimal(str(volatility_k)) * _MAX_ATR_OVER_PRICE


class SpreadModel:
    """Models bid/ask spread around a mid price — OHLCV bars have no real
    bid/ask, so this is a deliberate approximation (see the "Limitations"
    note in `docs/architecture.md`).

    A `BUY` fills at `mid + half_spread` (you cross the ask); a `SELL` fills
    at `mid - half_spread` (you cross the bid) — always worse than the mid,
    same as a real market order.

    Optional volatility scaling (Milestone 4.5 Phase B): when
    `volatility_k > 0` and a current ATR is supplied, half-spread widens by
    `k * min(ATR/mid, 0.05)` so fills are more expensive in volatile regimes.
    With `volatility_k == 0` (the default), behaviour is identical to the flat
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
        self._half_spread_fraction = half_spread_fraction_from_bps(spread_bps)
        self._volatility_k = Decimal(str(volatility_k))
        self._atr_period = atr_period
        self._max_half_spread_fraction = max_half_spread_fraction(spread_bps, volatility_k)

    @property
    def atr_period(self) -> int:
        return self._atr_period

    @property
    def volatility_enabled(self) -> bool:
        return self._volatility_k > 0

    @property
    def max_half_spread_fraction(self) -> Decimal:
        """Upper bound on half-spread / mid — shared with the cash guard."""
        return self._max_half_spread_fraction

    def fill_price(
        self,
        side: OrderSide,
        mid_price: Decimal,
        *,
        atr: Decimal | None = None,
    ) -> Decimal:
        half_fraction = self._half_spread_fraction
        if self._volatility_k > 0 and atr is not None and mid_price > 0:
            atr_over_price = min(atr / mid_price, _MAX_ATR_OVER_PRICE)
            half_fraction = half_fraction + self._volatility_k * atr_over_price
        half_spread = mid_price * half_fraction
        return mid_price + half_spread if side == OrderSide.BUY else mid_price - half_spread
