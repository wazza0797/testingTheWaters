from __future__ import annotations

import pandas as pd

from trading_platform.indicators.wilder import wilder_smoothing


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's Average True Range.

    True Range for each bar (after the first) is the greatest of:
    - `high - low`
    - `abs(high - previous_close)`
    - `abs(low - previous_close)`

    The first ATR (at index `period`) is the plain mean of the first `period`
    True Ranges; every ATR after uses Wilder's smoothing (`wilder.py`) — same
    smoothing family as RSI/ADX in this package.

    Returns a `float64` Series aligned to `close`'s index; the first `period`
    entries are `NaN` (need `period` True Ranges, which themselves need a
    previous close).
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    if not (len(high) == len(low) == len(close)):
        raise ValueError(f"high/low/close length mismatch: {len(high)}, {len(low)}, {len(close)}")

    result = pd.Series(float("nan"), index=close.index, dtype="float64")
    if len(close) <= period:
        return result

    high_v = high.to_numpy(dtype="float64")
    low_v = low.to_numpy(dtype="float64")
    close_v = close.to_numpy(dtype="float64")

    true_ranges: list[float] = []
    for i in range(1, len(close_v)):
        tr = max(
            high_v[i] - low_v[i],
            abs(high_v[i] - close_v[i - 1]),
            abs(low_v[i] - close_v[i - 1]),
        )
        true_ranges.append(tr)

    # true_ranges[0] corresponds to close index 1; wilder_smoothing seeds at
    # true_ranges index `period - 1`, which is close index `period`.
    smoothed = wilder_smoothing(pd.Series(true_ranges), period)
    for i in range(period - 1, len(true_ranges)):
        result.iloc[i + 1] = smoothed.iloc[i]

    return result
