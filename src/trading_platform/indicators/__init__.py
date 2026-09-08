"""Reusable technical indicators computed over a price series.

Pure, deterministic, side-effect-free functions with **no** dependency on
exchanges, the filesystem, or the event bus — see `docs/coding-standards.md`.
Indicators operate on `float64` (via pandas), not `Decimal`: they are
signal-generation inputs, not money/quantity values that get persisted or
accounted for. `Bar`/`Order`/`Fill` etc. remain `Decimal` everywhere else.

Indicators are asset-class agnostic: every function here operates on plain
OHLCV series with no crypto-specific (or any other venue-specific) semantics
baked in — see the composable-strategies milestone doc.
"""

from __future__ import annotations

from trading_platform.indicators.adx import compute_adx
from trading_platform.indicators.atr import compute_atr
from trading_platform.indicators.atr_pct import compute_atr_pct
from trading_platform.indicators.bollinger import compute_bollinger
from trading_platform.indicators.donchian import compute_donchian
from trading_platform.indicators.ema import compute_ema
from trading_platform.indicators.keltner import compute_keltner
from trading_platform.indicators.ma_slope import compute_ma_slope
from trading_platform.indicators.macd import compute_macd
from trading_platform.indicators.realized_vol import compute_realized_vol
from trading_platform.indicators.registry import (
    IndicatorRegistry,
    InputProfile,
    build_default_registry,
)
from trading_platform.indicators.rel_volume import compute_rel_volume
from trading_platform.indicators.returns import compute_returns
from trading_platform.indicators.roc import compute_roc
from trading_platform.indicators.rsi import compute_rsi
from trading_platform.indicators.sma import compute_sma
from trading_platform.indicators.stochastic import compute_stochastic
from trading_platform.indicators.supertrend import compute_supertrend
from trading_platform.indicators.utils import closes_from_bars, ohlc_from_bars, volumes_from_bars
from trading_platform.indicators.vol_percentile import compute_vol_percentile
from trading_platform.indicators.volume_breakout import compute_volume_breakout
from trading_platform.indicators.volume_roc import compute_volume_roc
from trading_platform.indicators.wilder import wilder_smoothing

__all__ = [
    "IndicatorRegistry",
    "InputProfile",
    "build_default_registry",
    "closes_from_bars",
    "compute_adx",
    "compute_atr",
    "compute_atr_pct",
    "compute_bollinger",
    "compute_donchian",
    "compute_ema",
    "compute_keltner",
    "compute_ma_slope",
    "compute_macd",
    "compute_realized_vol",
    "compute_rel_volume",
    "compute_returns",
    "compute_roc",
    "compute_rsi",
    "compute_sma",
    "compute_stochastic",
    "compute_supertrend",
    "compute_vol_percentile",
    "compute_volume_breakout",
    "compute_volume_roc",
    "ohlc_from_bars",
    "volumes_from_bars",
    "wilder_smoothing",
]
