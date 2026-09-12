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

    `assume_full_liquidity_when_no_volume` (default `False`, preserving the
    original "zero volume means zero fillable" behaviour for real
    trade-volume feeds like Binance) exists for venues whose bars carry no
    genuine trade-volume figure at all — e.g. spot-FX-derived data backing a
    CFD instrument (see `config/ig-gbpeur.yaml`), where every bar's `volume`
    is `0` by construction, not because nothing traded. Capping every fill to
    0% of a meaningless zero would silently wedge every order forever (the
    first pending order never resolves, so `PassThroughRiskEngine` rejects
    every later signal as "already pending" — see the GBPEUR research
    incident this was found from). When set, a bar with `bar_volume <= 0`
    fills the full `remaining_qty` instead of nothing, i.e. "assume
    effectively unlimited liquidity" rather than "assume no liquidity" —
    appropriate for deep FX majors, not a general-purpose default.
    """

    def __init__(
        self,
        volume_participation_rate: float,
        *,
        assume_full_liquidity_when_no_volume: bool = False,
    ) -> None:
        if not (0.0 < volume_participation_rate <= 1.0):
            raise ValueError(
                f"volume_participation_rate must be in (0.0, 1.0], got {volume_participation_rate}"
            )
        self._rate = Decimal(str(volume_participation_rate))
        self._assume_full_liquidity_when_no_volume = assume_full_liquidity_when_no_volume

    def fillable_quantity(self, bar_volume: Decimal, remaining_qty: Decimal) -> Decimal:
        if remaining_qty <= 0:
            return Decimal("0")
        if bar_volume <= 0:
            return remaining_qty if self._assume_full_liquidity_when_no_volume else Decimal("0")
        max_from_volume = bar_volume * self._rate
        return min(remaining_qty, max_from_volume)
