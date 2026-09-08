from __future__ import annotations

import pandas as pd


def wilder_smoothing(values: pd.Series, period: int) -> pd.Series:
    """Wilder's recursive smoothing over a series of per-bar values (e.g.
    true ranges for ATR, gains/losses for RSI, directional movement for ADX).

    The first smoothed value (at index `period - 1`, 0-based within
    `values`) is the plain mean of the first `period` values; every value
    after applies `avg = (prev_avg * (period - 1) + current) / period`. This
    is the exact recursive formula from Wilder's *New Concepts in Technical
    Trading Systems* (1978), shared by RSI, ATR, and ADX in this package —
    factored out here so all three use identical smoothing math instead of
    three slightly different hand-rolled copies.

    Returns a `float64` Series aligned to `values`' own index; entries
    before the seed are `NaN`. Callers are responsible for mapping this
    series' index back onto their own result's index (e.g. ATR/RSI derive
    `values` from bar-to-bar differences, which is one shorter than the
    original close series — see `indicators/atr.py` / `indicators/rsi.py`).
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")

    result = pd.Series(float("nan"), index=values.index, dtype="float64")
    v = values.to_numpy(dtype="float64")
    if len(v) < period:
        return result

    avg = float(v[:period].mean())
    result.iloc[period - 1] = avg
    for i in range(period, len(v)):
        avg = (avg * (period - 1) + v[i]) / period
        result.iloc[i] = avg
    return result
