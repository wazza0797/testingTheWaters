from __future__ import annotations

import pandas as pd

from trading_platform.indicators.ema import compute_ema
from trading_platform.indicators.sma import compute_sma


def compute_ma_slope(
    closes: pd.Series, period: int = 20, slope_bars: int = 5, ma_type: str = "sma"
) -> pd.Series:
    """Normalized slope of a moving average — trend quality without waiting
    for a full crossover.

    Computes the underlying MA (`sma` or `ema`, per `ma_type`) over
    `period`, then the **percentage** change of that MA over the trailing
    `slope_bars`: `(ma[i] - ma[i - slope_bars]) / abs(ma[i - slope_bars])`.
    Normalizing by the MA's own prior level (not a raw price delta) is what
    keeps this comparable across instruments/price levels — a BTC-priced MA
    and an FX-pair-priced MA can use the same threshold (see the
    asset-class-agnostic note in the composable-strategies milestone doc).

    Returns a `float64` Series aligned to `closes`' index; `NaN` until the
    MA itself has warmed up (`period - 1` bars) plus `slope_bars` more, and
    also wherever `ma[i - slope_bars]` is exactly zero (undefined percentage
    change — extremely unlikely for real price data, but guarded rather
    than raising `ZeroDivisionError`/producing `inf`).
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    if slope_bars < 1:
        raise ValueError(f"slope_bars must be >= 1, got {slope_bars}")
    if ma_type not in ("sma", "ema"):
        raise ValueError(f"ma_type must be 'sma' or 'ema', got {ma_type!r}")

    ma = compute_sma(closes, period) if ma_type == "sma" else compute_ema(closes, period)
    prior = ma.shift(slope_bars)
    result = (ma - prior) / prior.abs()
    return result.replace([float("inf"), float("-inf")], float("nan"))
