from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_platform.domain.models.bar import Bar
from trading_platform.indicators.utils import closes_from_bars


class TestClosesFromBars:
    def test_extracts_close_prices_as_float64(self, make_bar: Callable[..., Bar]) -> None:
        bars = [
            make_bar(close="100.5"),
            make_bar(close="101.25"),
            make_bar(close="99.75"),
        ]

        result = closes_from_bars(bars)

        assert result.dtype == "float64"
        assert list(result) == [100.5, 101.25, 99.75]

    def test_indexes_by_bar_timestamp(self, make_bar: Callable[..., Bar]) -> None:
        t0 = datetime(2024, 1, 1, tzinfo=UTC)
        t1 = datetime(2024, 1, 1, 1, tzinfo=UTC)
        bars = [make_bar(timestamp=t0, close="100"), make_bar(timestamp=t1, close="101")]

        result = closes_from_bars(bars)

        assert list(result.index) == [t0, t1]

    def test_empty_bar_list_returns_empty_series(self) -> None:
        result = closes_from_bars([])
        assert len(result) == 0
        assert result.dtype == "float64"

    def test_preserves_precision_within_float64_representation(
        self, make_bar: Callable[..., Bar]
    ) -> None:
        bars = [make_bar(open_="42123.45", high="42123.50", low="42123.40", close="42123.45")]
        result = closes_from_bars(bars)
        assert result.iloc[0] == pytest.approx(float(Decimal("42123.45")))
