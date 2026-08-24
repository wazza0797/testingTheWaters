from __future__ import annotations

from decimal import Decimal

from trading_platform.backtesting.models.fee_model import FeeModel
from trading_platform.backtesting.models.partial_fill_model import PartialFillModel
from trading_platform.backtesting.models.spread_model import SpreadModel
from trading_platform.domain.models.bar import Bar
from trading_platform.domain.models.fill import Fill
from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.domain.models.order import Order, OrderSide, OrderType
from trading_platform.execution.precision import round_qty


class FillSimulator:
    """Orchestrates one bar's fill attempt for one order: spread -> partial-fill
    cap -> fee. Shared, stateless logic — `SimBroker` owns the per-order
    latency/remaining-quantity bookkeeping (`OrderQueue`) and calls this once
    per (order, bar) pair once the order is past latency.

    Returns `None` when the order doesn't fill *at all* against this bar (a
    resting limit order the bar's range never reached) — as opposed to a
    `Fill` with `filled_qty == 0`, which never happens; a zero-quantity fill
    is meaningless and this returns `None` in that case too.
    """

    def __init__(
        self,
        spread_model: SpreadModel,
        fee_model: FeeModel,
        partial_fill_model: PartialFillModel,
        use_next_bar_open: bool,
    ) -> None:
        self._spread_model = spread_model
        self._fee_model = fee_model
        self._partial_fill_model = partial_fill_model
        self._use_next_bar_open = use_next_bar_open

    def simulate_fill(
        self, order: Order, remaining_qty: Decimal, bar: Bar, rules: InstrumentRules
    ) -> Fill | None:
        reference_price = bar.open if self._use_next_bar_open else bar.close

        if order.order_type == OrderType.MARKET:
            fill_price = self._spread_model.fill_price(order.side, reference_price)
            crosses_on_submission = True  # a market order always takes liquidity
        else:
            if order.price is None:
                raise ValueError(f"limit order {order.order_id!r} has no price")
            if not self._limit_price_reached(order.side, order.price, bar):
                return None
            fill_price = order.price
            crosses_on_submission = self._limit_crosses_market(
                order.side, order.price, reference_price
            )

        # Round down to the instrument's step size: `PartialFillModel` caps by
        # raw bar volume, with no notion of lot-size granularity, so a
        # volume-derived quantity can otherwise land between steps (or below
        # `min_qty`) in a way a real exchange would never accept. Rounding
        # down is always safe here — it only ever shrinks this fill, leaving
        # the (now slightly larger) remainder to be re-offered on a later
        # bar via `OrderQueue`, same as any other partial fill.
        filled_qty = round_qty(
            self._partial_fill_model.fillable_quantity(bar.volume, remaining_qty), rules
        )
        if filled_qty <= 0:
            return None

        fee_type = self._fee_model.fee_type_for(
            order.order_type, crosses_on_submission=crosses_on_submission
        )
        fee = self._fee_model.calculate_fee(filled_qty, fill_price, fee_type, rules)
        remaining_after = remaining_qty - filled_qty

        return Fill(
            order_id=order.order_id,
            correlation_id=order.correlation_id,
            symbol=order.symbol,
            side=order.side,
            filled_qty=filled_qty,
            remaining_qty=remaining_after,
            fill_price=fill_price,
            fee=fee,
            fee_type=fee_type,
            is_complete=remaining_after <= 0,
            timestamp=bar.timestamp,
        )

    @staticmethod
    def _limit_price_reached(side: OrderSide, limit_price: Decimal, bar: Bar) -> bool:
        """Whether this bar's high/low range crosses the limit price — the
        standard OHLCV-only approximation for "would a resting limit order
        have been hit" (see the Limitations note in `docs/architecture.md`).
        """
        if side == OrderSide.BUY:
            return bar.low <= limit_price
        return bar.high >= limit_price

    @staticmethod
    def _limit_crosses_market(
        side: OrderSide, limit_price: Decimal, reference_price: Decimal
    ) -> bool:
        """Whether a limit order would have crossed the spread immediately on
        submission (aggressive -> taker) rather than resting (passive ->
        maker-eligible). Approximated using the activation bar's own
        reference price, since no separate submission-time price snapshot is
        tracked.
        """
        if side == OrderSide.BUY:
            return limit_price >= reference_price
        return limit_price <= reference_price
