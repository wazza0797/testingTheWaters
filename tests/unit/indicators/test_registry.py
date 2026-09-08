from __future__ import annotations

import math

import pandas as pd
import pytest

from trading_platform.indicators.registry import (
    IndicatorRegistry,
    InputProfile,
    build_default_registry,
)
from trading_platform.indicators.sma import compute_sma


class TestIndicatorRegistry:
    def test_register_and_get_returns_the_same_function(self) -> None:
        registry = IndicatorRegistry()
        registry.register("sma", compute_sma)
        assert registry.get("sma") is compute_sma

    def test_register_defaults_to_close_profile(self) -> None:
        registry = IndicatorRegistry()
        registry.register("sma", compute_sma)
        assert registry.profile_for("sma") is InputProfile.CLOSE

    def test_register_accepts_explicit_profile(self) -> None:
        registry = IndicatorRegistry()
        registry.register("sma", compute_sma, profile=InputProfile.VOLUME)
        assert registry.profile_for("sma") is InputProfile.VOLUME

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

    def test_profile_for_unknown_name_raises_key_error(self) -> None:
        registry = IndicatorRegistry()
        with pytest.raises(KeyError, match="Unknown indicator"):
            registry.profile_for("sma")

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

    def test_compute_from_bars_returns_empty_series_for_no_bars(self) -> None:
        registry = IndicatorRegistry()
        registry.register("sma", compute_sma)

        result = registry.compute_from_bars("sma", [], period=2)

        assert result.empty


class TestBuildDefaultRegistry:
    def test_contains_the_full_indicator_catalog(self) -> None:
        registry = build_default_registry()
        assert registry.available() == sorted(
            [
                "adx",
                "atr",
                "atr_pct",
                "bb_lower",
                "bb_mid",
                "bb_upper",
                "bb_width",
                "di_minus",
                "di_plus",
                "donchian_lower",
                "donchian_mid",
                "donchian_upper",
                "ema",
                "keltner_lower",
                "keltner_mid",
                "keltner_upper",
                "ma_slope",
                "macd",
                "macd_hist",
                "macd_signal",
                "realized_vol",
                "rel_volume",
                "returns",
                "roc",
                "rsi",
                "sma",
                "stoch_d",
                "stoch_k",
                "supertrend",
                "supertrend_dir",
                "vol_percentile",
                "volume_breakout",
                "volume_roc",
            ]
        )

    def test_each_registered_indicator_is_computable_from_bars(self, make_bar) -> None:
        from datetime import UTC, datetime, timedelta

        registry = build_default_registry()
        start = datetime(2024, 1, 1, tzinfo=UTC)
        bars = [
            make_bar(
                timestamp=start + timedelta(hours=i),
                open_=str(100 + (i % 7) * 0.5),
                high=str(101 + (i % 7) * 0.5),
                low=str(99 + (i % 7) * 0.5),
                close=str(100 + (i % 7) * 0.5),
                volume=str(10 + (i % 5)),
            )
            for i in range(250)
        ]

        # sma/ema require an explicit period (no default); everything else
        # in the catalog has sensible defaults for every param.
        extra_params: dict[str, dict[str, int]] = {
            "sma": {"period": 14},
            "ema": {"period": 14},
        }

        for name in registry.available():
            series = registry.compute_from_bars(name, bars, **extra_params.get(name, {}))
            assert not series.empty, f"{name} returned an empty series"
            # Every indicator should have warmed up to a real value by bar 250.
            assert not math.isnan(series.iloc[-1]), f"{name} is still NaN at the end of warmup"
