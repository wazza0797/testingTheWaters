from __future__ import annotations

import pandas as pd

from trading_platform.indicators.realized_vol import compute_realized_vol


def compute_vol_percentile(
    closes: pd.Series, vol_period: int = 20, lookback: int = 100
) -> pd.Series:
    """Percentile rank (0-100) of the current `realized_vol` reading within
    its own trailing `lookback` history — the platform's primary
    **regime** volatility signal precisely because it is scale-free: "is
    volatility high *for this instrument, right now*" ports across a
    BTCUSDT chart and a CFD index chart without any per-instrument
    threshold tuning (see the asset-class-agnostic note in the
    composable-strategies milestone doc), unlike a raw vol number.

    For each bar, ranks the current `realized_vol(vol_period)` value against
    the `lookback` most recent realized-vol readings: `100 * (count of
    readings strictly below current) / (lookback - 1)`. `100` means the
    highest reading in the window, `0` the lowest.

    Returns a `float64` Series aligned to `closes`' index; `NaN` until a
    full `lookback` window of *valid* (non-NaN) realized-vol readings is
    available — i.e. after `realized_vol`'s own warmup, plus `lookback`
    more bars.
    """
    if vol_period < 1:
        raise ValueError(f"vol_period must be >= 1, got {vol_period}")
    if lookback < 2:
        raise ValueError(f"lookback must be >= 2, got {lookback}")

    vol = compute_realized_vol(closes, vol_period).to_numpy(dtype="float64")
    result = pd.Series(float("nan"), index=closes.index, dtype="float64")

    for i in range(len(vol)):
        current = vol[i]
        if current != current:  # NaN
            continue
        start = max(0, i - lookback + 1)
        window = vol[start : i + 1]
        valid = window[window == window]  # drop NaN
        if len(valid) < lookback:
            continue
        rank = int((valid < current).sum())
        result.iloc[i] = 100.0 * rank / (len(valid) - 1)

    return result
