from __future__ import annotations

import math

import pandas as pd
import pytest

from trading_platform.indicators.registry import IndicatorRegistry, build_default_registry
from trading_platform.indicators.sma import compute_sma


class TestIndicatorRegistry:
    def test_register_and_get_returns_the_same_function(self) -> None:
        registry = IndicatorRegistry()
        registry.register("sma", compute_sma)
        assert registry.get("sma") is compute_sma

    def test_registering_duplicate_name_raises(self) -> None:
        registry = IndicatorRegistry()
        registry.register("sma", compute_sma)
        with pytest.raises(ValueError):
            registry.register("sma", compute_sma)

    def test_unknown_name_raises_key_error_listing_available(self) -> None:
        registry = IndicatorRegistry()
        registry.register("sma", compute_sma)
        with pytest.raises(KeyError, match="sma"):
            registry.get("ema")

    def test_available_returns_sorted_registered_names(self) -> None:
        registry = IndicatorRegistry()
        registry.register("rsi", compute_sma)
        registry.register("ema", compute_sma)
        registry.register("sma", compute_sma)
        assert registry.available() == ["ema", "rsi", "sma"]

    def test_compute_dispatches_to_registered_function_with_params(self) -> None:
        registry = IndicatorRegistry()
        registry.register("sma", compute_sma)
        closes = pd.Series([1.0, 2.0, 3.0, 4.0])

        result = registry.compute("sma", closes, period=2)

        assert result.iloc[1] == pytest.approx(1.5)
        assert result.iloc[3] == pytest.approx(3.5)


class TestBuildDefaultRegistry:
    def test_contains_all_milestone_two_indicators(self) -> None:
        registry = build_default_registry()
        assert registry.available() == ["ema", "rsi", "sma"]

    def test_each_registered_indicator_is_callable_end_to_end(self) -> None:
        registry = build_default_registry()
        closes = pd.Series([float(i) for i in range(1, 30)])

        sma_result = registry.compute("sma", closes, period=5)
        ema_result = registry.compute("ema", closes, period=5)
        rsi_result = registry.compute("rsi", closes, period=14)

        assert not math.isnan(sma_result.iloc[-1])
        assert not math.isnan(ema_result.iloc[-1])
        assert not math.isnan(rsi_result.iloc[-1])
