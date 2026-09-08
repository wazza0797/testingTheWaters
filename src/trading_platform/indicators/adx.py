from __future__ import annotations

import pandas as pd

from trading_platform.indicators.wilder import wilder_smoothing


def compute_adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.DataFrame:
    """Average Directional Index, plus its two directional components.

    Standard Wilder construction (uses the shared `wilder_smoothing` — see
    `indicators/wilder.py`, also used by RSI/ATR):

    1. Per bar (after the first): `+DM = max(high[i] - high[i-1], 0)` if that
       exceeds the down-move (`low[i-1] - low[i]`), else 0; `-DM` is the
       mirror image. `TR` is the same True Range as `compute_atr`.
    2. Wilder-smooth `+DM`, `-DM`, and `TR` independently over `period`.
    3. `+DI = 100 * smoothed(+DM) / smoothed(TR)`, `-DI` likewise.
    4. `DX = 100 * |+DI - -DI| / (+DI + -DI)`.
    5. `ADX` = `DX` Wilder-smoothed again over `period` (a second round of
       smoothing on top of step 2 — this double-smoothing is why ADX warms
       up roughly `2 * period` bars in, not `period`).

    Returns a `float64` DataFrame with columns `plus_di`, `minus_di`, `adx`,
    aligned to `close`'s index. All three are `NaN` during warmup; `+DI`/`-DI`
    are `0.0` (not `NaN`) whenever `smoothed(TR)` is exactly zero (a
    perfectly flat market — no true range at all), and `DX`/`ADX` are `0.0`
    in that same case (matching the "no directional movement" reading rather
    than a spurious `NaN`/division error).
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    if not (len(high) == len(low) == len(close)):
        raise ValueError(f"high/low/close length mismatch: {len(high)}, {len(low)}, {len(close)}")

    plus_di_result = pd.Series(float("nan"), index=close.index, dtype="float64")
    minus_di_result = pd.Series(float("nan"), index=close.index, dtype="float64")
    adx_result = pd.Series(float("nan"), index=close.index, dtype="float64")
    if len(close) <= 2 * period:
        return pd.DataFrame(
            {"plus_di": plus_di_result, "minus_di": minus_di_result, "adx": adx_result}
        )

    high_v = high.to_numpy(dtype="float64")
    low_v = low.to_numpy(dtype="float64")
    close_v = close.to_numpy(dtype="float64")

    plus_dm: list[float] = []
    minus_dm: list[float] = []
    true_ranges: list[float] = []
    for i in range(1, len(close_v)):
        up_move = high_v[i] - high_v[i - 1]
        down_move = low_v[i - 1] - low_v[i]
        plus_dm.append(up_move if (up_move > down_move and up_move > 0) else 0.0)
        minus_dm.append(down_move if (down_move > up_move and down_move > 0) else 0.0)
        true_ranges.append(
            max(
                high_v[i] - low_v[i],
                abs(high_v[i] - close_v[i - 1]),
                abs(low_v[i] - close_v[i - 1]),
            )
        )

    smoothed_plus_dm = wilder_smoothing(pd.Series(plus_dm), period)
    smoothed_minus_dm = wilder_smoothing(pd.Series(minus_dm), period)
    smoothed_tr = wilder_smoothing(pd.Series(true_ranges), period)

    plus_di = pd.Series(float("nan"), index=range(len(true_ranges)), dtype="float64")
    minus_di = pd.Series(float("nan"), index=range(len(true_ranges)), dtype="float64")
    dx = pd.Series(float("nan"), index=range(len(true_ranges)), dtype="float64")
    for i in range(period - 1, len(true_ranges)):
        tr = smoothed_tr.iloc[i]
        if tr == 0:
            plus_di.iloc[i] = 0.0
            minus_di.iloc[i] = 0.0
            dx.iloc[i] = 0.0
            continue
        pd_i = 100.0 * smoothed_plus_dm.iloc[i] / tr
        md_i = 100.0 * smoothed_minus_dm.iloc[i] / tr
        plus_di.iloc[i] = pd_i
        minus_di.iloc[i] = md_i
        denom = pd_i + md_i
        dx.iloc[i] = 0.0 if denom == 0 else 100.0 * abs(pd_i - md_i) / denom

    adx_smoothed = wilder_smoothing(dx.dropna(), period)

    for i in range(period - 1, len(true_ranges)):
        close_idx = i + 1  # true_ranges[i] corresponds to close index i + 1
        plus_di_result.iloc[close_idx] = plus_di.iloc[i]
        minus_di_result.iloc[close_idx] = minus_di.iloc[i]
        if i in adx_smoothed.index and not pd.isna(adx_smoothed.loc[i]):
            adx_result.iloc[close_idx] = adx_smoothed.loc[i]

    return pd.DataFrame({"plus_di": plus_di_result, "minus_di": minus_di_result, "adx": adx_result})
