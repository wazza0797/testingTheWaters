"""Reusable technical indicators computed over a price series.

Pure, deterministic, side-effect-free functions with **no** dependency on
exchanges, the filesystem, or the event bus — see `docs/coding-standards.md`.
Indicators operate on `float64` (via pandas), not `Decimal`: they are
signal-generation inputs, not money/quantity values that get persisted or
accounted for. `Bar`/`Order`/`Fill` etc. remain `Decimal` everywhere else.
"""

from __future__ import annotations

from trading_platform.indicators.ema import compute_ema
from trading_platform.indicators.registry import IndicatorRegistry, build_default_registry
from trading_platform.indicators.rsi import compute_rsi
from trading_platform.indicators.sma import compute_sma
from trading_platform.indicators.utils import closes_from_bars

__all__ = [
    "IndicatorRegistry",
    "build_default_registry",
    "closes_from_bars",
    "compute_ema",
    "compute_rsi",
    "compute_sma",
]
