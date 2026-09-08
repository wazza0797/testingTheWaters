from __future__ import annotations

from collections.abc import Callable, Sequence
from enum import StrEnum

import pandas as pd

from trading_platform.domain.models.bar import Bar
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

IndicatorFn = Callable[..., pd.Series]


class InputProfile(StrEnum):
    """What series an indicator function needs, driving how
    `IndicatorRegistry.compute_from_bars` extracts inputs from a bar
    sequence — this is what lets `strategies/context.py` call any
    registered indicator by name with zero per-name special cases (e.g. the
    old hard-coded `if name == "atr"`).

    Every registered function's positional signature must match its
    profile, in this order:

    - `CLOSE`: `fn(closes, **params)`
    - `OHLC`: `fn(high, low, close, **params)`
    - `OHLCV`: `fn(high, low, close, volume, **params)`
    - `VOLUME`: `fn(volume, **params)`
    """

    CLOSE = "close"
    OHLC = "ohlc"
    OHLCV = "ohlcv"
    VOLUME = "volume"


def _column(df_fn: Callable[..., pd.DataFrame], column: str) -> IndicatorFn:
    """Adapter for a multi-line indicator (e.g. MACD, Bollinger): registers
    one named registry entry per output column, so `ctx.indicator(name, ...)`
    keeps returning a single latest float regardless of how many lines the
    underlying indicator computes (see `docs/milestones/` composable
    strategies write-up). Recomputes the full indicator on every call — no
    incremental/streaming computation, same accepted trade-off as every
    other indicator in this package (see M2 milestone doc).
    """

    def _adapter(*args: pd.Series, **kwargs: object) -> pd.Series:
        return df_fn(*args, **kwargs)[column]

    return _adapter


class IndicatorRegistry:
    """Named lookup for indicator functions, keyed by a stable string name.

    Exists so strategies (Milestone 3+) can reference an indicator from
    config (e.g. `{"indicator": "sma", "period": 20}`) without importing
    indicator modules directly — the registry is the only thing that needs
    to know every indicator that exists.
    """

    def __init__(self) -> None:
        self._indicators: dict[str, IndicatorFn] = {}
        self._profiles: dict[str, InputProfile] = {}

    def register(
        self, name: str, fn: IndicatorFn, *, profile: InputProfile = InputProfile.CLOSE
    ) -> None:
        if name in self._indicators:
            raise ValueError(f"Indicator '{name}' is already registered")
        self._indicators[name] = fn
        self._profiles[name] = profile

    def get(self, name: str) -> IndicatorFn:
        try:
            return self._indicators[name]
        except KeyError as exc:
            raise KeyError(f"Unknown indicator '{name}'. Available: {self.available()}") from exc

    def profile_for(self, name: str) -> InputProfile:
        if name not in self._profiles:
            raise KeyError(f"Unknown indicator '{name}'. Available: {self.available()}")
        return self._profiles[name]

    def compute(self, name: str, *series: pd.Series, **params: object) -> pd.Series:
        """Dispatch to the registered function for `name` with whichever
        raw series its profile needs, positionally (e.g. `compute("sma",
        closes, period=20)`, `compute("atr", high, low, close, period=14)`).

        Most callers driven by bar sequences should prefer
        `compute_from_bars`, which extracts the right series from `Bar`s
        automatically based on `profile_for(name)`; this method stays useful
        for tests and any caller that already has the series on hand.
        """
        return self.get(name)(*series, **params)

    def compute_from_bars(self, name: str, bars: Sequence[Bar], **params: object) -> pd.Series:
        """Compute indicator `name` directly from a bar sequence, extracting
        whichever series its registered `InputProfile` requires.

        This is what `strategies/context.py::DefaultStrategyContext.indicator`
        calls — replaces the old per-name special case (`if name == "atr"`)
        with one generic, profile-driven code path that works identically
        for close-only, OHLC, OHLCV, and volume-only indicators.
        """
        if not bars:
            return pd.Series(dtype="float64")
        profile = self.profile_for(name)
        if profile is InputProfile.CLOSE:
            return self.compute(name, closes_from_bars(bars), **params)
        if profile is InputProfile.OHLC:
            high, low, close = ohlc_from_bars(bars)
            return self.compute(name, high, low, close, **params)
        if profile is InputProfile.OHLCV:
            high, low, close = ohlc_from_bars(bars)
            volume = volumes_from_bars(bars)
            return self.compute(name, high, low, close, volume, **params)
        if profile is InputProfile.VOLUME:
            return self.compute(name, volumes_from_bars(bars), **params)
        raise AssertionError(f"unhandled InputProfile {profile!r}")  # pragma: no cover

    def available(self) -> list[str]:
        return sorted(self._indicators)


def build_default_registry() -> IndicatorRegistry:
    """Registry pre-populated with every indicator this package ships."""
    registry = IndicatorRegistry()

    # Trend
    registry.register("sma", compute_sma, profile=InputProfile.CLOSE)
    registry.register("ema", compute_ema, profile=InputProfile.CLOSE)
    registry.register("ma_slope", compute_ma_slope, profile=InputProfile.CLOSE)
    registry.register("macd", _column(compute_macd, "macd"), profile=InputProfile.CLOSE)
    registry.register("macd_signal", _column(compute_macd, "signal"), profile=InputProfile.CLOSE)
    registry.register("macd_hist", _column(compute_macd, "hist"), profile=InputProfile.CLOSE)
    registry.register("adx", _column(compute_adx, "adx"), profile=InputProfile.OHLC)
    registry.register("di_plus", _column(compute_adx, "plus_di"), profile=InputProfile.OHLC)
    registry.register("di_minus", _column(compute_adx, "minus_di"), profile=InputProfile.OHLC)
    registry.register(
        "donchian_upper", _column(compute_donchian, "upper"), profile=InputProfile.OHLC
    )
    registry.register(
        "donchian_lower", _column(compute_donchian, "lower"), profile=InputProfile.OHLC
    )
    registry.register("donchian_mid", _column(compute_donchian, "mid"), profile=InputProfile.OHLC)
    registry.register("supertrend", _column(compute_supertrend, "value"), profile=InputProfile.OHLC)
    registry.register(
        "supertrend_dir", _column(compute_supertrend, "direction"), profile=InputProfile.OHLC
    )

    # Momentum
    registry.register("rsi", compute_rsi, profile=InputProfile.CLOSE)
    registry.register("roc", compute_roc, profile=InputProfile.CLOSE)
    registry.register("returns", compute_returns, profile=InputProfile.CLOSE)
    registry.register("stoch_k", _column(compute_stochastic, "k"), profile=InputProfile.OHLC)
    registry.register("stoch_d", _column(compute_stochastic, "d"), profile=InputProfile.OHLC)

    # Volatility
    registry.register("atr", compute_atr, profile=InputProfile.OHLC)
    registry.register("atr_pct", compute_atr_pct, profile=InputProfile.OHLC)
    registry.register("bb_mid", _column(compute_bollinger, "mid"), profile=InputProfile.CLOSE)
    registry.register("bb_upper", _column(compute_bollinger, "upper"), profile=InputProfile.CLOSE)
    registry.register("bb_lower", _column(compute_bollinger, "lower"), profile=InputProfile.CLOSE)
    registry.register("bb_width", _column(compute_bollinger, "width"), profile=InputProfile.CLOSE)
    registry.register("keltner_mid", _column(compute_keltner, "mid"), profile=InputProfile.OHLC)
    registry.register("keltner_upper", _column(compute_keltner, "upper"), profile=InputProfile.OHLC)
    registry.register("keltner_lower", _column(compute_keltner, "lower"), profile=InputProfile.OHLC)
    registry.register("realized_vol", compute_realized_vol, profile=InputProfile.CLOSE)
    registry.register("vol_percentile", compute_vol_percentile, profile=InputProfile.CLOSE)

    # Volume (optional confirmation — see asset-class-agnostic note in the
    # composable-strategies milestone doc)
    registry.register("rel_volume", compute_rel_volume, profile=InputProfile.VOLUME)
    registry.register("volume_roc", compute_volume_roc, profile=InputProfile.VOLUME)
    registry.register("volume_breakout", compute_volume_breakout, profile=InputProfile.VOLUME)

    return registry
