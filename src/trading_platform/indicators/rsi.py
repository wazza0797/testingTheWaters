from __future__ import annotations

import pandas as pd


def compute_rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's smoothed Relative Strength Index.

    The original formula from Wilder's *New Concepts in Technical Trading
    Systems* (1978) — what TradingView, StockCharts, and most brokers mean by
    "RSI" (as distinct from a simpler, less common simple-moving-average
    variant).

    For each bar-to-bar change: `gain = max(change, 0)`, `loss = max(-change, 0)`.

    - First average gain/loss (at index `period`) = plain mean of the first
      `period` gains/losses.
    - Every average after: `avg = (prev_avg * (period - 1) + current) / period`.
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
    gains = [c if c > 0 else 0.0 for c in changes]
    losses = [-c if c < 0 else 0.0 for c in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    result.iloc[period] = _rsi_from_averages(avg_gain, avg_loss)

    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        result.iloc[i + 1] = _rsi_from_averages(avg_gain, avg_loss)

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
