from __future__ import annotations

from datetime import UTC, datetime

from trading_platform.market_data.gaps import find_gaps


class TestNoGaps:
    def test_empty_list_has_no_gaps(self, make_bar) -> None:
        assert find_gaps([], "1h") == []

    def test_single_bar_has_no_gaps(self, make_bar) -> None:
        bars = [make_bar(timestamp=datetime(2024, 1, 1, tzinfo=UTC))]
        assert find_gaps(bars, "1h") == []

    def test_contiguous_bars_have_no_gaps(self, make_bar) -> None:
        bars = [
            make_bar(timestamp=datetime(2024, 1, 1, hour=0, tzinfo=UTC)),
            make_bar(timestamp=datetime(2024, 1, 1, hour=1, tzinfo=UTC)),
            make_bar(timestamp=datetime(2024, 1, 1, hour=2, tzinfo=UTC)),
            make_bar(timestamp=datetime(2024, 1, 1, hour=3, tzinfo=UTC)),
        ]
        assert find_gaps(bars, "1h") == []


class TestGapsDetected:
    def test_single_missing_bar_is_reported(self, make_bar) -> None:
        bars = [
            make_bar(timestamp=datetime(2024, 1, 1, hour=0, tzinfo=UTC)),
            # hour=1 missing
            make_bar(timestamp=datetime(2024, 1, 1, hour=2, tzinfo=UTC)),
        ]

        gaps = find_gaps(bars, "1h")

        assert len(gaps) == 1
        assert gaps[0].after == datetime(2024, 1, 1, hour=0, tzinfo=UTC)
        assert gaps[0].before == datetime(2024, 1, 1, hour=2, tzinfo=UTC)
        assert gaps[0].missing_count == 1

    def test_multi_bar_gap_reports_correct_missing_count(self, make_bar) -> None:
        bars = [
            make_bar(timestamp=datetime(2024, 1, 1, hour=0, tzinfo=UTC)),
            # hours 1, 2, 3 missing
            make_bar(timestamp=datetime(2024, 1, 1, hour=4, tzinfo=UTC)),
        ]

        gaps = find_gaps(bars, "1h")

        assert len(gaps) == 1
        assert gaps[0].missing_count == 3

    def test_multiple_separate_gaps_are_all_reported(self, make_bar) -> None:
        bars = [
            make_bar(timestamp=datetime(2024, 1, 1, hour=0, tzinfo=UTC)),
            make_bar(timestamp=datetime(2024, 1, 1, hour=2, tzinfo=UTC)),  # gap 1
            make_bar(timestamp=datetime(2024, 1, 1, hour=3, tzinfo=UTC)),
            make_bar(timestamp=datetime(2024, 1, 1, hour=6, tzinfo=UTC)),  # gap 2
        ]

        gaps = find_gaps(bars, "1h")

        assert len(gaps) == 2
        assert gaps[0].missing_count == 1
        assert gaps[1].missing_count == 2

    def test_uses_the_given_timeframe_interval(self, make_bar) -> None:
        # 2h apart is contiguous for a 4h timeframe, but a gap for 1h.
        bars = [
            make_bar(timestamp=datetime(2024, 1, 1, hour=0, tzinfo=UTC)),
            make_bar(timestamp=datetime(2024, 1, 1, hour=2, tzinfo=UTC)),
        ]

        assert find_gaps(bars, "4h") == []
        assert len(find_gaps(bars, "1h")) == 1
