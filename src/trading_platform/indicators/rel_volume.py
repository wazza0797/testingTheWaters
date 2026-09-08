from __future__ import annotations

import pandas as pd

from trading_platform.indicators.sma import compute_sma


def compute_rel_volume(volume: pd.Series, period: int = 20) -> pd.Series:
    """Relative volume: current bar volume divided by its trailing average
    (`volume / SMA(volume, period)`) — optional breakout confirmation, not a
    hard platform dependency (see the asset-class-agnostic note in the
    composable-strategies milestone doc: some CFD/FX venues report thin or
    meaningless volume).

    Returns a `float64` Series aligned to `volume`'s index; the first
    `period - 1` entries are `NaN`, and any bar whose trailing average
    volume is exactly zero is `NaN` too (a market reporting zero volume
    throughout the window — guarded rather than producing `inf`, so a
    volume-optional recipe never crashes on a venue with no usable volume).
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    avg = compute_sma(volume, period)
    result = volume / avg
    return result.replace([float("inf"), float("-inf")], float("nan"))
