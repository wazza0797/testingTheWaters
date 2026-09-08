from __future__ import annotations

import pandas as pd

from trading_platform.indicators.atr import compute_atr


def compute_atr_pct(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    """ATR expressed as a fraction of price (`ATR(period) / close`) —
    the cross-asset-comparable volatility gate this platform prefers over
    raw ATR in strategy recipes, since raw ATR is denominated in the
    instrument's own price units (meaningless to compare a BTCUSDT ATR
    against a 1.2345-quoted FX-pair ATR; see the asset-class-agnostic note
    in the composable-strategies milestone doc).

    Returns a `float64` Series aligned to `close`'s index; `NaN` while ATR
    is warming up, and also wherever `close` is exactly zero (guarded
    rather than producing `inf`, though a real close of zero should never
    happen — see `Bar.__post_init__`).
    """
    atr = compute_atr(high, low, close, period=period)
    result = atr / close
    return result.replace([float("inf"), float("-inf")], float("nan"))
