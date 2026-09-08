from __future__ import annotations

import pandas as pd

from trading_platform.indicators.atr import compute_atr


def compute_supertrend(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 10, multiplier: float = 3.0
) -> pd.DataFrame:
    """Supertrend: an ATR-based trend-following overlay, popular as a
    portable long/flat filter across instruments and timeframes.

    Standard construction:

    - `basic_upper = (high + low) / 2 + multiplier * ATR(period)`
    - `basic_lower = (high + low) / 2 - multiplier * ATR(period)`
    - `final_upper[i] = basic_upper[i]` if `basic_upper[i] < final_upper[i-1]`
      or `close[i-1] > final_upper[i-1]`, else `final_upper[i-1]` (the band
      only ever tightens while price stays below it — it does not loosen
      bar-to-bar unless price closes back through it).
    - `final_lower` is the mirror image.
    - `direction[i] = 1` (uptrend) once `close[i] > final_upper[i-1]`;
      `direction[i] = -1` (downtrend) once `close[i] < final_lower[i-1]`;
      otherwise carries the previous bar's direction.
    - `value[i] = final_lower[i]` while `direction[i] == 1` (the trailing
      long stop), else `final_upper[i]` (the trailing short/flip level).

    Returns a `float64` DataFrame with columns `value`, `direction`
    (`1.0`/`-1.0`), aligned to `close`'s index. `NaN` (`direction` too, as
    `NaN`, not `0.0`, to make "not yet determined" unambiguous from a real
    downtrend reading) until `ATR` warms up; the very first bar with a
    defined ATR seeds `direction = 1` if `close` is above the midpoint band,
    else `-1`.
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    if multiplier <= 0:
        raise ValueError(f"multiplier must be > 0, got {multiplier}")

    atr = compute_atr(high, low, close, period=period)
    mid = (high + low) / 2.0
    basic_upper = mid + multiplier * atr
    basic_lower = mid - multiplier * atr

    n = len(close)
    value = pd.Series(float("nan"), index=close.index, dtype="float64")
    direction = pd.Series(float("nan"), index=close.index, dtype="float64")

    final_upper = float("nan")
    final_lower = float("nan")
    prev_direction = 0.0
    close_v = close.to_numpy(dtype="float64")

    for i in range(n):
        bu = basic_upper.iloc[i]
        bl = basic_lower.iloc[i]
        if bu != bu:  # NaN: ATR not warmed up yet
            continue

        if final_upper != final_upper:  # first bar with a defined ATR
            final_upper = bu
            final_lower = bl
            prev_direction = 1.0 if close_v[i] > mid.iloc[i] else -1.0
        else:
            prev_close = close_v[i - 1]
            final_upper = bu if (bu < final_upper or prev_close > final_upper) else final_upper
            final_lower = bl if (bl > final_lower or prev_close < final_lower) else final_lower

            if close_v[i] > final_upper:
                prev_direction = 1.0
            elif close_v[i] < final_lower:
                prev_direction = -1.0
            # else: keep prev_direction unchanged (trend intact)

        direction.iloc[i] = prev_direction
        value.iloc[i] = final_lower if prev_direction == 1.0 else final_upper

    return pd.DataFrame({"value": value, "direction": direction})
