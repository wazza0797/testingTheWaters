from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_platform.analytics.state import RunningPerformanceState
from trading_platform.domain.models.fill import FeeType, Fill
from trading_platform.domain.models.order import OrderSide

UTC_TS = datetime(2024, 1, 1, tzinfo=UTC)


def _fill(
    side: OrderSide,
    price: str,
    *,
    day: int = 0,
    qty: str = "1",
    fee: str = "0",
) -> Fill:
    return Fill(
        order_id="o1",
        correlation_id="c1",
        symbol="BTC/USDT",
        side=side,
        filled_qty=Decimal(qty),
        remaining_qty=Decimal("0"),
        fill_price=Decimal(price),
        fee=Decimal(fee),
        fee_type=FeeType.TAKER,
        is_complete=True,
        timestamp=UTC_TS + timedelta(days=day),
    )


class TestSnapshotMetrics:
    def test_ending_equity_and_return_reflect_realized_pnl(self) -> None:
        state = RunningPerformanceState(starting_cash=Decimal("10000"))
        state.record_fill(_fill(OrderSide.BUY, "100", day=0))
        state.record_fill(_fill(OrderSide.SELL, "110", day=1))

        metrics = state.snapshot_metrics()

        assert metrics.round_trip_count == 1
        assert metrics.avg_trade_pnl == Decimal("10")
        assert metrics.ending_equity == Decimal("10010")
        assert metrics.total_return_pct == Decimal("0.1")

    def test_empty_state_keeps_starting_cash(self) -> None:
        state = RunningPerformanceState(starting_cash=Decimal("5000"))
        metrics = state.snapshot_metrics()
        assert metrics.ending_equity == Decimal("5000")
        assert metrics.total_return_pct == Decimal("0")
        assert metrics.round_trip_count == 0
