from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from trading_platform.indicators.atr import compute_atr
from trading_platform.indicators.ema import compute_ema
from trading_platform.indicators.rsi import compute_rsi
from trading_platform.indicators.sma import compute_sma

IndicatorFn = Callable[..., pd.Series]


def _atr_from_closes(
    closes: pd.Series,
    period: int = 14,
    *,
    high: pd.Series | None = None,
    low: pd.Series | None = None,
) -> pd.Series:
    """Registry adapter: ATR needs high/low in addition to close.

    Call via `registry.compute("atr", closes, high=..., low=..., period=...)`
    or prefer `StrategyContext.indicator("atr", bars)` which supplies OHLC.
    """
    if high is None or low is None:
        raise ValueError(
            "atr requires high= and low= Series kwargs (or use "
            "StrategyContext.indicator('atr', bars) which supplies them)"
        )
    return compute_atr(high, low, closes, period=period)


class IndicatorRegistry:
    """Named lookup for indicator functions, keyed by a stable string name.

    Exists so strategies (Milestone 3) can reference an indicator from
    config (e.g. `{"indicator": "sma", "period": 20}`) without importing
    indicator modules directly — the registry is the only thing that needs
    to know every indicator that exists.
    """

    def __init__(self) -> None:
        self._indicators: dict[str, IndicatorFn] = {}

    def register(self, name: str, fn: IndicatorFn) -> None:
        if name in self._indicators:
            raise ValueError(f"Indicator '{name}' is already registered")
        self._indicators[name] = fn

    def get(self, name: str) -> IndicatorFn:
        try:
            return self._indicators[name]
        except KeyError as exc:
            raise KeyError(f"Unknown indicator '{name}'. Available: {self.available()}") from exc

    def compute(self, name: str, closes: pd.Series, **params: object) -> pd.Series:
        return self.get(name)(closes, **params)

    def available(self) -> list[str]:
        return sorted(self._indicators)


def build_default_registry() -> IndicatorRegistry:
    """Registry pre-populated with every indicator this package ships."""
    registry = IndicatorRegistry()
    registry.register("sma", compute_sma)
    registry.register("ema", compute_ema)
    registry.register("rsi", compute_rsi)
    registry.register("atr", _atr_from_closes)
    return registry
