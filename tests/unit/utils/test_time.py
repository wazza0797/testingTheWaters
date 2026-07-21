from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from trading_platform.utils.time import to_utc, utc_now


class TestUtcNow:
    def test_returns_timezone_aware_datetime(self) -> None:
        now = utc_now()
        assert now.tzinfo is not None


class TestToUtc:
    def test_naive_datetime_assumed_utc(self) -> None:
        naive = datetime(2024, 1, 1, 12, 0, 0)
        result = to_utc(naive)
        assert result.tzinfo == UTC
        assert result.hour == 12

    def test_aware_datetime_converted_to_utc(self) -> None:
        eastern = timezone(timedelta(hours=-5))
        aware = datetime(2024, 1, 1, 12, 0, 0, tzinfo=eastern)
        result = to_utc(aware)
        assert result.tzinfo == UTC
        assert result.hour == 17
