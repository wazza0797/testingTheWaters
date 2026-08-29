from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_platform.analytics.benchmark import buy_and_hold_return_pct
from trading_platform.domain.models.bar import Bar


def _bar(day: int, close: str) -> Bar:
    return Bar(
        symbol="BTC/USDT",
        timeframe="1d",
        timestamp=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=day),
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("1"),
    )


class TestBuyAndHold:
    def test_return_from_first_to_last_close(self) -> None:
        bars = (_bar(0, "100"), _bar(1, "110"), _bar(2, "120"))
        assert buy_and_hold_return_pct(bars) == Decimal("20")

    def test_respects_start_end_window(self) -> None:
        bars = (_bar(0, "100"), _bar(10, "150"), _bar(20, "200"), _bar(30, "250"))
        start = datetime(2024, 1, 11, tzinfo=UTC)
        end = datetime(2024, 2, 1, tzinfo=UTC)
        # Day 10 is Jan 11 (≥ start); days 10/20/30 → 150 → 250 = +66.666...%
        assert buy_and_hold_return_pct(bars, start=start, end=end) == (
            Decimal("250") - Decimal("150")
        ) / Decimal("150") * Decimal("100")

    def test_insufficient_bars_returns_none(self) -> None:
        assert buy_and_hold_return_pct((_bar(0, "100"),)) is None
        assert buy_and_hold_return_pct(()) is None
