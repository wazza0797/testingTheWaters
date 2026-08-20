from __future__ import annotations

import pandas as pd


def compute_ema(closes: pd.Series, period: int) -> pd.Series:
    """Exponential moving average, seeded with a simple average.

    Seeds at index `period - 1` with the plain average of the first `period`
    closes, then applies the standard recursive formula
    `ema[i] = (close[i] - ema[i-1]) * multiplier + ema[i-1]` for every bar
    after, where `multiplier = 2 / (period + 1)`. This is the convention
    used by TradingView and most trading platforms/brokers.

    Deliberately **not** `pandas.Series.ewm(...)`: pandas' default
    (`adjust=True`) computes a differently-weighted average over the entire
    history rather than this textbook recursive formula, and would silently
    produce different numbers than every other platform means by "EMA".

    Returns a `float64` Series with the same length/index as `closes`; the
    first `period - 1` entries are `NaN`.
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")

    values = closes.to_numpy(dtype="float64")
    result = pd.Series(float("nan"), index=closes.index, dtype="float64")
    if len(values) < period:
        return result

    multiplier = 2.0 / (period + 1)
    seed = float(values[:period].mean())
    result.iloc[period - 1] = seed

    previous = seed
    for i in range(period, len(values)):
        current = (values[i] - previous) * multiplier + previous
        result.iloc[i] = current
        previous = current

    return result
