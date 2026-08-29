from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_platform.analytics.metrics import compute_metrics, max_drawdown_pct
from trading_platform.analytics.trades import RoundTrip
from trading_platform.backtesting.result import EquityPoint
from trading_platform.domain.models.fill import FeeType, Fill
from trading_platform.domain.models.order import OrderSide

UTC_TS = datetime(2024, 1, 1, tzinfo=UTC)


def _equity(day: int, equity: str) -> EquityPoint:
    return EquityPoint(timestamp=UTC_TS + timedelta(days=day), equity=Decimal(equity))


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


class TestMaxDrawdown:
    def test_known_equity_curve_drawdown(self) -> None:
        # 100 → 120 → 90 → 110 = peak 120, trough 90 → -25%
        curve = (
            _equity(0, "100"),
            _equity(1, "120"),
            _equity(2, "90"),
            _equity(3, "110"),
        )
        assert max_drawdown_pct(curve) == Decimal("-25")

    def test_empty_curve_is_zero(self) -> None:
        assert max_drawdown_pct([]) == Decimal("0")


class TestComputeMetrics:
    def test_win_rate_and_profit_factor(self) -> None:
        trips = (_trip("10"), _trip("5"), _trip("-4"))
        metrics = compute_metrics(
            [],
            (_equity(0, "100"), _equity(1, "111")),
            Decimal("100"),
            bars_processed=10,
            round_trips=trips,
        )

        assert metrics.round_trip_count == 3
        assert metrics.win_count == 2
        assert metrics.loss_count == 1
        assert metrics.win_rate == 2 / 3
        assert metrics.profit_factor == 15 / 4
        assert metrics.avg_trade_pnl == Decimal("11") / Decimal("3")

    def test_sharpe_on_steady_upward_equity_is_positive(self) -> None:
        curve = tuple(_equity(i, str(100 + i)) for i in range(40))
        metrics = compute_metrics(
            [],
            curve,
            Decimal("100"),
            bars_processed=40,
            round_trips=(),
        )
        assert metrics.sharpe_daily is not None
        assert metrics.sharpe_daily > 1.0

    def test_total_return_and_fees(self) -> None:
        fill = Fill(
            order_id="o1",
            correlation_id="c1",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            filled_qty=Decimal("1"),
            remaining_qty=Decimal("0"),
            fill_price=Decimal("100"),
            fee=Decimal("2.5"),
            fee_type=FeeType.TAKER,
            is_complete=True,
            timestamp=UTC_TS,
        )
        metrics = compute_metrics(
            [fill],
            (_equity(0, "100"), _equity(1, "110")),
            Decimal("100"),
            bars_processed=2,
        )
        assert metrics.total_return_pct == Decimal("10")
        assert metrics.total_fees == Decimal("2.5")
