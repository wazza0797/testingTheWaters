from __future__ import annotations

import pandas as pd


def compute_donchian(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20
) -> pd.DataFrame:
    """Donchian channel: rolling highest-high / lowest-low breakout bands.

    `upper = rolling_max(high, period)`, `lower = rolling_min(low, period)`,
    `mid = (upper + lower) / 2`. `close` is accepted but unused — kept so
    this function has the same `(high, low, close, **params)` positional
    shape as every other `InputProfile.OHLC` indicator (see
    `indicators/registry.py`), rather than needing a dedicated profile just
    for the one indicator that only needs high/low.

    Returns a `float64` DataFrame with columns `upper`, `lower`, `mid`,
    aligned to `high`'s index. The first `period - 1` entries of every
    column are `NaN`.
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")

    upper = high.rolling(window=period, min_periods=period).max()
    lower = low.rolling(window=period, min_periods=period).min()
    mid = (upper + lower) / 2.0

    return pd.DataFrame({"upper": upper, "lower": lower, "mid": mid})
