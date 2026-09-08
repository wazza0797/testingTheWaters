from __future__ import annotations

import pandas as pd

from trading_platform.indicators.ema import compute_ema


def compute_macd(
    closes: pd.Series,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> pd.DataFrame:
    """Moving Average Convergence/Divergence.

    `macd = EMA(fast_period) - EMA(slow_period)`; `signal = EMA(macd,
    signal_period)`; `hist = macd - signal`. Uses this package's
    `compute_ema` (recursive formula seeded with a simple average — see
    `indicators/ema.py`), applied a second time to `macd` itself for the
    signal line, exactly as every reference implementation does.

    Returns a `float64` DataFrame with columns `macd`, `signal`, `hist`,
    aligned to `closes`' index. `macd` is `NaN` until the slow EMA warms up;
    `signal`/`hist` need `signal_period - 1` further non-NaN `macd` values,
    so they warm up later than `macd` itself.
    """
    if fast_period < 1 or slow_period < 1 or signal_period < 1:
        raise ValueError(
            "fast_period, slow_period, signal_period must all be >= 1, got "
            f"{fast_period}/{slow_period}/{signal_period}"
        )
    if fast_period >= slow_period:
        raise ValueError(
            f"fast_period ({fast_period}) must be strictly less than slow_period ({slow_period})"
        )

    fast_ema = compute_ema(closes, fast_period)
    slow_ema = compute_ema(closes, slow_period)
    macd = fast_ema - slow_ema
    signal = compute_ema(macd.dropna(), signal_period).reindex(macd.index)
    hist = macd - signal

    return pd.DataFrame({"macd": macd, "signal": signal, "hist": hist})
