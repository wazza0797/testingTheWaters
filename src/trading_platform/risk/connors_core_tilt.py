"""Locked portfolio risk constants for the Connors core+tilt runner.

Do not re-derive from research in the runner — wire these as-is.
See docs/strategies/connors-core-tilt-runner.md.
"""

from __future__ import annotations

# US500 + Nasdaq shared bucket (initial conservative deployment).
US_BUCKET_RISK_PCT = 0.015

# Sharpe-weighted split of the US bucket (OOS RSI<15 standalone Sharpes).
US500_BUCKET_WEIGHT = 0.39
NASDAQ_BUCKET_WEIGHT = 0.61

US500_TILT_RISK_PCT = US_BUCKET_RISK_PCT * US500_BUCKET_WEIGHT  # ≈ 0.585%
NASDAQ_TILT_RISK_PCT = US_BUCKET_RISK_PCT * NASDAQ_BUCKET_WEIGHT  # ≈ 0.915%

# DAX separate leg — no three-way simultaneous scaler.
DAX_TILT_RISK_PCT = 0.01

# Vol throttle: skip new tilt entries when rv(10)/mean(rv10,60) >= this.
VOL_THROTTLE_RATIO = 1.5

# Market keys used in multi-overlay configs / exposure monitors.
MARKET_US500 = "ig-us500"
MARKET_NASDAQ = "ig-us-tech100"
MARKET_DAX = "ig-dax-daily"

TILT_RISK_BY_OVERLAY: dict[str, float] = {
    MARKET_US500: US500_TILT_RISK_PCT,
    MARKET_NASDAQ: NASDAQ_TILT_RISK_PCT,
    MARKET_DAX: DAX_TILT_RISK_PCT,
}
