from __future__ import annotations

import pandas as pd

from trading_platform.indicators.wilder import wilder_smoothing


def compute_rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's smoothed Relative Strength Index.

    The original formula from Wilder's *New Concepts in Technical Trading
    Systems* (1978) — what TradingView, StockCharts, and most brokers mean by
    "RSI" (as distinct from a simpler, less common simple-moving-average
    variant).

    For each bar-to-bar change: `gain = max(change, 0)`, `loss = max(-change, 0)`.
    Both gain and loss series are smoothed independently via Wilder's
    recursive average (`wilder.py` — shared with ATR/ADX): first average (at
    index `period`) = plain mean of the first `period` gains/losses; every
    average after = `(prev_avg * (period - 1) + current) / period`.

    - `RS = avg_gain / avg_loss`; `RSI = 100 - 100 / (1 + RS)`.
    - By definition: `RSI = 100` when `avg_loss == 0` (all gains), `RSI = 0`
      when `avg_gain == 0` (all losses), `RSI = 50` when both are zero (no
      price movement at all — a neutral reading, since RS is undefined).

    Returns a `float64` Series with the same length/index as `closes`; the
    first `period` entries are `NaN` (one change is needed per bar, plus
    `period` changes to seed the first average).
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")

    values = closes.to_numpy(dtype="float64")
    result = pd.Series(float("nan"), index=closes.index, dtype="float64")
    if len(values) <= period:
        return result

    changes = values[1:] - values[:-1]
    gains = pd.Series([c if c > 0 else 0.0 for c in changes])
    losses = pd.Series([-c if c < 0 else 0.0 for c in changes])

    avg_gains = wilder_smoothing(gains, period)
    avg_losses = wilder_smoothing(losses, period)

    # avg_{gain,loss} seed at gains-index `period - 1`, which is close index
    # `period` (changes[i] is the step from close[i] to close[i + 1]).
    for i in range(period - 1, len(changes)):
        result.iloc[i + 1] = _rsi_from_averages(avg_gains.iloc[i], avg_losses.iloc[i])

    return result


def _rsi_from_averages(avg_gain: float, avg_loss: float) -> float:
    if avg_gain == 0 and avg_loss == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))
