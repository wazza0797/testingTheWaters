from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_platform.analytics.metrics import PerformanceMetrics
from trading_platform.analytics.significance import (
    SignificanceFlag,
    bootstrap_return_ci,
    compute_flags,
)
from trading_platform.analytics.trades import RoundTrip

UTC_TS = datetime(2024, 1, 1, tzinfo=UTC)


def _trip(pnl: str) -> RoundTrip:
    return RoundTrip(
        symbol="BTC/USDT",
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        exit_price=Decimal("110"),
        entry_time=UTC_TS,
        exit_time=UTC_TS + timedelta(hours=1),
        pnl=Decimal(pnl),
        fees=Decimal("0"),
        is_partial=False,
    )


def _metrics(*, trips: int, bars: int, daily: int = 5) -> PerformanceMetrics:
    return PerformanceMetrics(
        starting_cash=Decimal("10000"),
        ending_equity=Decimal("10000"),
        total_return_pct=Decimal("0"),
        max_drawdown_pct=Decimal("0"),
        sharpe_daily=None,
        round_trip_count=trips,
        win_count=0,
        loss_count=0,
        win_rate=None,
        profit_factor=None,
        avg_trade_pnl=None,
        total_fees=Decimal("0"),
        bars_processed=bars,
        daily_return_count=daily,
    )


class TestComputeFlags:
    def test_low_sample_size_when_under_threshold(self) -> None:
        trips = tuple(_trip("1") for _ in range(10))
        flags, _ = compute_flags(_metrics(trips=10, bars=1000), trips)
        assert any(f.flag == SignificanceFlag.LOW_SAMPLE_SIZE for f in flags)

    def test_no_low_sample_size_when_enough_trips(self) -> None:
        trips = tuple(_trip("1") for _ in range(50))
        flags, _ = compute_flags(
            _metrics(trips=50, bars=1000, daily=40),
            trips,
            min_daily_returns_for_sharpe=30,
        )
        assert not any(f.flag == SignificanceFlag.LOW_SAMPLE_SIZE for f in flags)

    def test_low_bar_count_flag(self) -> None:
        flags, _ = compute_flags(_metrics(trips=50, bars=100), ())
        assert any(f.flag == SignificanceFlag.LOW_BAR_COUNT for f in flags)


class TestBootstrapCI:
    def test_reproducible_with_seed(self) -> None:
        trips = tuple(_trip(str(i - 5)) for i in range(20))
        a = bootstrap_return_ci(trips, iterations=200, seed=7)
        b = bootstrap_return_ci(trips, iterations=200, seed=7)
        assert a is not None and b is not None
        assert a.lower == b.lower
        assert a.upper == b.upper

    def test_wide_ci_when_variance_high(self) -> None:
        # Half large wins, half large losses → CI typically spans zero
        trips = tuple(_trip("100") for _ in range(10)) + tuple(_trip("-100") for _ in range(10))
        ci = bootstrap_return_ci(trips, iterations=500, seed=1)
        assert ci is not None
        assert ci.spans_zero

    def test_empty_trips_returns_none(self) -> None:
        assert bootstrap_return_ci(()) is None
