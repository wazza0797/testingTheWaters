from __future__ import annotations

import pandas as pd
import pytest

from trading_platform.indicators.realized_vol import compute_realized_vol


class TestComputeRealizedVol:
    def test_zero_for_flat_prices(self) -> None:
        closes = pd.Series([100.0 for _ in range(30)])
        result = compute_realized_vol(closes, period=10)
        valid = result.dropna()
        assert (valid == 0.0).all()

    def test_higher_for_more_volatile_series(self) -> None:
        calm = pd.Series([100.0 + (i % 2) * 0.1 for i in range(30)])
        wild = pd.Series([100.0 + (i % 2) * 10 for i in range(30)])

        calm_vol = compute_realized_vol(calm, period=10).iloc[-1]
        wild_vol = compute_realized_vol(wild, period=10).iloc[-1]

        assert wild_vol > calm_vol

    def test_rejects_period_less_than_one(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0])
        with pytest.raises(ValueError, match="period"):
            compute_realized_vol(s, period=0)

    def test_short_series_is_all_nan(self) -> None:
        s = pd.Series([1.0, 2.0])
        result = compute_realized_vol(s, period=10)
        assert result.isna().all()

    def test_does_not_mutate_input(self) -> None:
        closes = pd.Series([100.0, 101.0, 99.0, 102.0])
        original = closes.copy()
        compute_realized_vol(closes, period=2)
        pd.testing.assert_series_equal(closes, original)
