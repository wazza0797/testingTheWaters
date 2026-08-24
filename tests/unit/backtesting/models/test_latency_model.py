from __future__ import annotations

import pytest

from trading_platform.backtesting.models.latency_model import LatencyModel


class TestLatencyModel:
    def test_stores_the_configured_latency_bars(self) -> None:
        model = LatencyModel(latency_bars=3)

        assert model.latency_bars == 3

    def test_zero_is_a_valid_latency(self) -> None:
        model = LatencyModel(latency_bars=0)

        assert model.latency_bars == 0

    def test_negative_latency_bars_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            LatencyModel(latency_bars=-1)
