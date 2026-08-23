from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from trading_platform.indicators.ema import compute_ema
from trading_platform.indicators.rsi import compute_rsi
from trading_platform.indicators.sma import compute_sma

IndicatorFn = Callable[..., pd.Series]


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
    """Registry pre-populated with every indicator this milestone ships."""
    registry = IndicatorRegistry()
    registry.register("sma", compute_sma)
    registry.register("ema", compute_ema)
    registry.register("rsi", compute_rsi)
    return registry
