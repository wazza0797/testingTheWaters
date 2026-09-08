from __future__ import annotations

import numpy as np
import pandas as pd


def compute_returns(closes: pd.Series, period: int = 1, kind: str = "simple") -> pd.Series:
    """N-bar return over `period` bars, as a fraction (not a percentage —
    distinct from `roc`, which scales by 100; both are provided since
    strategy recipes reference either convention).

    `kind="simple"`: `close[i] / close[i - period] - 1`.
    `kind="log"`: `ln(close[i] / close[i - period])`.

    Returns a `float64` Series aligned to `closes`' index; the first
    `period` entries are `NaN`, and any bar whose reference close is
    exactly zero (or, for `kind="log"`, whose ratio is zero or negative —
    never possible for real positive prices) is `NaN` rather than `inf`/`nan`
    from an unguarded division or `log`.
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    if kind not in ("simple", "log"):
        raise ValueError(f"kind must be 'simple' or 'log', got {kind!r}")

    prior = closes.shift(period)
    ratio = closes / prior
    result: pd.Series
    if kind == "simple":
        result = ratio - 1.0
    else:
        with np.errstate(divide="ignore", invalid="ignore"):
            log_values = np.log(ratio)
        result = pd.Series(log_values, index=closes.index, dtype="float64")
    return result.replace([float("inf"), float("-inf")], float("nan"))
