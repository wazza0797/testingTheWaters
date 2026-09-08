from __future__ import annotations

import pandas as pd


def compute_stochastic(
    high: pd.Series, low: pd.Series, close: pd.Series, k_period: int = 14, d_period: int = 3
) -> pd.DataFrame:
    """Stochastic Oscillator: `%K`/`%D`.

    `%K = 100 * (close - rolling_min(low, k_period)) / (rolling_max(high,
    k_period) - rolling_min(low, k_period))`; `%D = SMA(%K, d_period)` (the
    "slow" %K signal line).

    Returns a `float64` DataFrame with columns `k`, `d`, aligned to
    `close`'s index. `k` is `NaN` during its warmup (`k_period - 1` bars)
    and wherever the rolling range is exactly zero (a perfectly flat market
    over the window — guarded rather than producing `inf`); `d` needs
    `d_period - 1` further non-NaN `k` values.
    """
    if k_period < 1:
        raise ValueError(f"k_period must be >= 1, got {k_period}")
    if d_period < 1:
        raise ValueError(f"d_period must be >= 1, got {d_period}")

    lowest_low = low.rolling(window=k_period, min_periods=k_period).min()
    highest_high = high.rolling(window=k_period, min_periods=k_period).max()
    denom = highest_high - lowest_low
    k = (100.0 * (close - lowest_low) / denom).replace([float("inf"), float("-inf")], float("nan"))
    d = k.rolling(window=d_period, min_periods=d_period).mean()

    return pd.DataFrame({"k": k, "d": d})
