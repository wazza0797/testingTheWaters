from __future__ import annotations

from datetime import timedelta

import pytest

from trading_platform.domain.errors import MarketDataError
from trading_platform.market_data.timeframe import timeframe_to_timedelta


class TestValidTimeframes:
    @pytest.mark.parametrize(
        ("timeframe", "expected"),
        [
            ("1s", timedelta(seconds=1)),
            ("30s", timedelta(seconds=30)),
            ("1m", timedelta(minutes=1)),
            ("5m", timedelta(minutes=5)),
            ("15m", timedelta(minutes=15)),
            ("1h", timedelta(hours=1)),
            ("4h", timedelta(hours=4)),
            ("1d", timedelta(days=1)),
            ("1w", timedelta(weeks=1)),
        ],
    )
    def test_parses_expected_interval(self, timeframe: str, expected: timedelta) -> None:
        assert timeframe_to_timedelta(timeframe) == expected


class TestMalformedTimeframes:
    @pytest.mark.parametrize("timeframe", ["", "h", "1", "1x", "-1h", "0h", "1.5h", "1H"])
    def test_raises_on_malformed_input(self, timeframe: str) -> None:
        with pytest.raises(MarketDataError):
            timeframe_to_timedelta(timeframe)
