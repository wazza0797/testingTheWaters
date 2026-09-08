from __future__ import annotations

import pandas as pd


def compute_roc(closes: pd.Series, period: int = 10) -> pd.Series:
    """Rate of Change: percentage price change over `period` bars —
    `(close[i] - close[i - period]) / close[i - period] * 100`.

    Returns a `float64` Series aligned to `closes`' index; the first
    `period` entries are `NaN`, and any bar whose reference close is exactly
    zero is `NaN` too (guarded rather than producing `inf`).
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    prior = closes.shift(period)
    result = (closes - prior) / prior * 100.0
    return result.replace([float("inf"), float("-inf")], float("nan"))
