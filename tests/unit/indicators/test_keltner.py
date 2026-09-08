from __future__ import annotations

import pandas as pd
import pytest

from trading_platform.indicators.keltner import compute_keltner


class TestComputeKeltner:
    def test_upper_always_at_or_above_lower(self) -> None:
        high = pd.Series([100.0 + i for i in range(40)])
        low = pd.Series([98.0 + i for i in range(40)])
        close = pd.Series([99.0 + i for i in range(40)])

        result = compute_keltner(high, low, close, period=10, atr_period=5, multiplier=2.0)
        valid = result.dropna()
        assert (valid["upper"] >= valid["lower"]).all()

    def test_mid_tracks_ema_of_close(self) -> None:
        from trading_platform.indicators.ema import compute_ema

        high = pd.Series([100.0 + i for i in range(40)])
        low = pd.Series([98.0 + i for i in range(40)])
        close = pd.Series([99.0 + i for i in range(40)])

        result = compute_keltner(high, low, close, period=10, atr_period=5)
        expected_mid = compute_ema(close, 10)
        pd.testing.assert_series_equal(result["mid"], expected_mid, check_names=False)

    def test_wider_multiplier_widens_the_channel(self) -> None:
        high = pd.Series([100.0 + i for i in range(40)])
        low = pd.Series([98.0 + i for i in range(40)])
        close = pd.Series([99.0 + i for i in range(40)])

        narrow = compute_keltner(high, low, close, period=10, atr_period=5, multiplier=1.0)
        wide = compute_keltner(high, low, close, period=10, atr_period=5, multiplier=3.0)

        assert (wide["upper"].iloc[-1] - wide["lower"].iloc[-1]) > (
            narrow["upper"].iloc[-1] - narrow["lower"].iloc[-1]
        )

    def test_rejects_period_less_than_one(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0])
        with pytest.raises(ValueError, match="period"):
            compute_keltner(s, s, s, period=0)

    def test_rejects_non_positive_multiplier(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0])
        with pytest.raises(ValueError, match="multiplier"):
            compute_keltner(s, s, s, multiplier=0)
