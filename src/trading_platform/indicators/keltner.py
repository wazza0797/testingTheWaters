from __future__ import annotations

import pandas as pd

from trading_platform.indicators.atr import compute_atr
from trading_platform.indicators.ema import compute_ema


def compute_keltner(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 20,
    atr_period: int = 10,
    multiplier: float = 2.0,
) -> pd.DataFrame:
    """Keltner Channel: an EMA envelope widened/narrowed by ATR — paired
    with Bollinger Bands to detect a volatility squeeze (price inside both
    bands => compression; Bollinger breaking outside Keltner => expansion).

    `mid = EMA(period)`; `upper/lower = mid +/- multiplier * ATR(atr_period)`.

    Returns a `float64` DataFrame with columns `mid`, `upper`, `lower`,
    aligned to `close`'s index. `NaN` until both the EMA and ATR have warmed
    up (whichever is slower).
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    if multiplier <= 0:
        raise ValueError(f"multiplier must be > 0, got {multiplier}")

    mid = compute_ema(close, period)
    atr = compute_atr(high, low, close, period=atr_period)
    upper = mid + multiplier * atr
    lower = mid - multiplier * atr

    return pd.DataFrame({"mid": mid, "upper": upper, "lower": lower})
