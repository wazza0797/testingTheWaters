from __future__ import annotations

from decimal import Decimal


class PartialFillModel:
    """Caps how much of an order's remaining quantity can fill against a
    single bar, as a fraction of that bar's traded volume.

    OHLCV data has no order-book depth, so `volume_participation_rate` is a
    deliberately simple stand-in: "don't assume we can fill more than X% of
    what actually traded this bar." A large order therefore fills across
    multiple bars — the caller (`SimBroker`) re-offers the unfilled remainder
    on each subsequent bar via `OrderQueue` until it's fully filled.
    """

    def __init__(self, volume_participation_rate: float) -> None:
        if not (0.0 < volume_participation_rate <= 1.0):
            raise ValueError(
                f"volume_participation_rate must be in (0.0, 1.0], got {volume_participation_rate}"
            )
        self._rate = Decimal(str(volume_participation_rate))

    def fillable_quantity(self, bar_volume: Decimal, remaining_qty: Decimal) -> Decimal:
        if bar_volume <= 0 or remaining_qty <= 0:
            return Decimal("0")
        max_from_volume = bar_volume * self._rate
        return min(remaining_qty, max_from_volume)
