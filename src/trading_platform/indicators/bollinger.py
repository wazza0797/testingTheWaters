from __future__ import annotations

import pandas as pd

from trading_platform.indicators.sma import compute_sma


def compute_bollinger(closes: pd.Series, period: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    """Bollinger Bands: an SMA envelope widened/narrowed by rolling standard
    deviation — mean-reversion bands and, via `width`, a squeeze/expansion
    volatility read.

    `mid = SMA(period)`; `upper/lower = mid +/- num_std * rolling_std(period)`
    (population-style `ddof=0`, the conventional Bollinger definition);
    `width = (upper - lower) / mid` — expressed as a fraction of price so it
    is comparable across instruments, same rationale as `atr_pct`.

    Returns a `float64` DataFrame with columns `mid`, `upper`, `lower`,
    `width`, aligned to `closes`' index. The first `period - 1` entries of
    every column are `NaN`; `width` is additionally `NaN` wherever `mid` is
    exactly zero.
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    if num_std <= 0:
        raise ValueError(f"num_std must be > 0, got {num_std}")

    mid = compute_sma(closes, period)
    std = closes.rolling(window=period, min_periods=period).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    width = ((upper - lower) / mid).replace([float("inf"), float("-inf")], float("nan"))

    return pd.DataFrame({"mid": mid, "upper": upper, "lower": lower, "width": width})
