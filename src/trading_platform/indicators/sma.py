from __future__ import annotations

import pandas as pd


def compute_sma(closes: pd.Series, period: int) -> pd.Series:
    """Simple moving average: unweighted mean of the trailing `period` closes.

    Returns a `float64` Series with the same length and index as `closes`.
    The first `period - 1` entries are `NaN` — pandas' idiomatic way of
    representing "not enough history yet" — rather than raising or shrinking
    the output, so the result always aligns index-for-index against the
    input series.
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    return closes.rolling(window=period, min_periods=period).mean()
