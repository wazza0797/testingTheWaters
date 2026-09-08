from __future__ import annotations

import pandas as pd


def compute_realized_vol(closes: pd.Series, period: int = 20) -> pd.Series:
    """Realized volatility: rolling standard deviation of simple bar-to-bar
    returns — a return-based volatility measure that stays meaningful even
    on venues where volume is thin or unreliable (see the
    asset-class-agnostic note in the composable-strategies milestone doc),
    unlike volume-derived measures.

    Deliberately **not annualized** (no fixed "bars per year" assumption
    baked in, which would silently be wrong across timeframes and asset
    classes — a 1h crypto bar and a 1h FX-CFD bar do not share a trading
    calendar). Callers comparing across timeframes should do so via
    `vol_percentile` (rank within its own history) rather than the raw
    value.

    Returns a `float64` Series aligned to `closes`' index; the first
    `period` entries are `NaN` (needs `period` returns, which need
    `period + 1` closes).
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    returns = closes.pct_change()
    return returns.rolling(window=period, min_periods=period).std(ddof=0)
