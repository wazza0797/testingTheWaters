from __future__ import annotations

import pandas as pd

from trading_platform.indicators.sma import compute_sma


def compute_volume_breakout(volume: pd.Series, period: int = 20, k: float = 2.0) -> pd.Series:
    """Volume breakout flag: `1.0` when the current bar's volume exceeds
    `k` times its trailing average, else `0.0` — a boolean encoded as a
    float so it composes directly with the `compare` condition leaf (e.g.
    `{indicator: volume_breakout, ..., op: "==", value: 1}`), same
    convention used everywhere else indicators are consumed by name (see
    `strategies/rules/`).

    Optional breakout confirmation, not a hard platform dependency — see the
    asset-class-agnostic note in the composable-strategies milestone doc.

    Returns a `float64` Series aligned to `volume`'s index; `NaN` during
    warmup (the first `period - 1` entries), never `1.0`/`0.0` until the
    trailing average is defined.
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    if k <= 0:
        raise ValueError(f"k must be > 0, got {k}")
    avg = compute_sma(volume, period)
    is_breakout = volume > (k * avg)
    result = is_breakout.astype("float64")
    result[avg.isna()] = float("nan")
    return result
