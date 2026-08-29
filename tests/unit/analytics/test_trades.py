from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_platform.analytics.trades import reconstruct_round_trips
from trading_platform.domain.models.fill import FeeType, Fill
from trading_platform.domain.models.order import OrderSide

UTC_TS = datetime(2024, 1, 1, tzinfo=UTC)


def _fill(
    side: OrderSide,
    filled_qty: Decimal,
    fill_price: Decimal,
    fee: Decimal = Decimal("0"),
    *,
    timestamp: datetime | None = None,
    symbol: str = "BTC/USDT",
) -> Fill:
    return Fill(
        order_id="o1",
        correlation_id="c1",
        symbol=symbol,
        side=side,
        filled_qty=filled_qty,
        remaining_qty=Decimal("0"),
        fill_price=fill_price,
        fee=fee,
        fee_type=FeeType.TAKER,
        is_complete=True,
        timestamp=timestamp or UTC_TS,
    )


class TestReconstructRoundTrips:
    def test_empty_fills_yield_no_trips(self) -> None:
        assert reconstruct_round_trips([]) == ()

    def test_single_buy_sell_pnl_includes_fees(self) -> None:
        # Buy 1 @ 100 fee 1 → amortized entry 101; sell @ 110 fee 1 → pnl = 110-1-101 = 8
        fills = (
            _fill(OrderSide.BUY, Decimal("1"), Decimal("100"), fee=Decimal("1")),
            _fill(
                OrderSide.SELL,
                Decimal("1"),
                Decimal("110"),
                fee=Decimal("1"),
                timestamp=UTC_TS + timedelta(hours=1),
            ),
        )

        trips = reconstruct_round_trips(fills)

        assert len(trips) == 1
        assert trips[0].pnl == Decimal("8")
        assert trips[0].fees == Decimal("2")
        assert trips[0].is_partial is False
        assert trips[0].is_winner is True

    def test_partial_sell_splits_pnl(self) -> None:
        fills = (
            _fill(OrderSide.BUY, Decimal("2"), Decimal("100"), fee=Decimal("2")),
            _fill(
                OrderSide.SELL,
                Decimal("1"),
                Decimal("110"),
                fee=Decimal("1"),
                timestamp=UTC_TS + timedelta(hours=1),
            ),
        )

        trips = reconstruct_round_trips(fills)

        assert len(trips) == 1
        assert trips[0].quantity == Decimal("1")
        assert trips[0].is_partial is True
        # entry amortized: (200+2)/2 = 101; exit fee full 1 on sold qty
        # pnl = 110 - 1 - 101 = 8
        assert trips[0].pnl == Decimal("8")

    def test_multiple_sequential_round_trips(self) -> None:
        t1 = UTC_TS
        t2 = UTC_TS + timedelta(hours=1)
        t3 = UTC_TS + timedelta(hours=2)
        t4 = UTC_TS + timedelta(hours=3)
        fills = (
            _fill(OrderSide.BUY, Decimal("1"), Decimal("100"), timestamp=t1),
            _fill(OrderSide.SELL, Decimal("1"), Decimal("110"), timestamp=t2),
            _fill(OrderSide.BUY, Decimal("1"), Decimal("105"), timestamp=t3),
            _fill(OrderSide.SELL, Decimal("1"), Decimal("100"), timestamp=t4),
        )

        trips = reconstruct_round_trips(fills)

        assert len(trips) == 2
        assert trips[0].pnl == Decimal("10")
        assert trips[1].pnl == Decimal("-5")
        assert trips[1].is_winner is False

    def test_sell_without_open_position_is_ignored(self) -> None:
        fills = (_fill(OrderSide.SELL, Decimal("1"), Decimal("100")),)

        assert reconstruct_round_trips(fills) == ()
