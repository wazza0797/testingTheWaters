from __future__ import annotations

import uuid
from collections.abc import Mapping
from decimal import Decimal

from trading_platform.domain.models.bar import Bar
from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.domain.models.order import Order, OrderSide, OrderType
from trading_platform.domain.models.position import Position
from trading_platform.domain.models.signal import Signal, SignalType
from trading_platform.domain.ports.portfolio import IPortfolioView
from trading_platform.domain.ports.risk import IPendingOrderTracker, RiskDecision
from trading_platform.execution.precision import round_qty
from trading_platform.risk.sizing import EquityFractionSizer

# Placeholder, always overwritten by RiskHandler with the triggering event's
# correlation_id before publishing (mirrors how StrategyHandler stamps
# Signal.strategy_name — see strategies/handler.py) so this engine doesn't
# need to know about events at all.
_PENDING_CORRELATION_ID = "pending"


class PassThroughRiskEngine:
    """The initial (and, per the roadmap, likely long-lived) `IRiskEngine`:
    approves everything it can structurally act on and rejects the rest —
    no max-position, drawdown, or other real risk rules exist yet.

    Long-only, since BTC/USDT is spot (no margin/shorting):
    - `BUY` while already holding a position: rejected — this engine never
      averages in or pyramids.
    - `SELL`/`CLOSE` while flat: rejected — nothing to close.
    - `BUY` while flat: sized via `EquityFractionSizer` against current
      equity and the triggering bar's close (`Signal` has no price of its
      own — see `SignalGenerated.bar`), then shrunk if needed so its
      worst-case cost (fee plus a safety buffer for the spread/slippage the
      *actual* fill may incur once latency elapses) never exceeds available
      cash — see `_affordable_quantity`. Rejected outright if that leaves
      nothing affordable.
    - `SELL`/`CLOSE` while holding a position: closes the *entire* position
      (no partial-reduce policy exists yet).
    - **Any** signal while an earlier order for the same symbol is still
      outstanding (queued on latency, or only partially filled): rejected.
      `IPortfolioView.position_for` only reflects *filled* fills, so without
      this check a second `BUY` could be approved before the first one's
      fill ever lands in the ledger (violating "never averages in"), or a
      `SELL` could be approved while a `BUY` is still partially filling. See
      `IPendingOrderTracker`.

    Rejections here are trading-policy-level (`RiskRejected`) and distinct
    from `execution/order_validator.py`'s exchange-rule-level rejections
    (`OrderRejected`) — e.g. a sized quantity that rounds to exactly zero is
    rejected here, while a positive-but-too-small quantity is rejected later,
    downstream, by the `OrderValidator` against `InstrumentRules.min_qty`.
    """

    def __init__(
        self,
        portfolio: IPortfolioView,
        instrument_rules: Mapping[str, InstrumentRules],
        sizer: EquityFractionSizer,
        pending_orders: IPendingOrderTracker,
        cash_safety_buffer_pct: float = 0.001,
    ) -> None:
        self._portfolio = portfolio
        self._instrument_rules = instrument_rules
        self._sizer = sizer
        self._pending_orders = pending_orders
        self._cash_safety_buffer_pct = Decimal(str(cash_safety_buffer_pct))

    def evaluate(self, signal: Signal, bar: Bar) -> RiskDecision:
        rules = self._instrument_rules.get(signal.symbol)
        if rules is None:
            return RiskDecision(
                order=None, rejection_reason=f"no instrument rules for {signal.symbol!r}"
            )

        if self._pending_orders.has_pending_order(signal.symbol):
            return RiskDecision(
                order=None,
                rejection_reason=(
                    f"an order for {signal.symbol} is already pending "
                    f"(not yet filled/rejected); ignoring {signal.signal_type.value} signal"
                ),
            )

        position = self._portfolio.position_for(signal.symbol)

        if signal.signal_type == SignalType.BUY:
            return self._evaluate_buy(signal, bar, rules, position)
        if signal.signal_type in (SignalType.SELL, SignalType.CLOSE):
            return self._evaluate_close(signal, bar, position)
        return RiskDecision(
            order=None, rejection_reason=f"unsupported signal_type {signal.signal_type!r}"
        )

    def _evaluate_buy(
        self, signal: Signal, bar: Bar, rules: InstrumentRules, position: Position | None
    ) -> RiskDecision:
        if position is not None and not position.is_flat:
            return RiskDecision(
                order=None,
                rejection_reason=(
                    f"already in a position for {signal.symbol} "
                    f"(qty={position.quantity}); ignoring BUY signal"
                ),
            )

        price = bar.close
        equity = self._portfolio.equity({signal.symbol: price})
        quantity = self._sizer.size(equity, price, rules)
        if quantity <= 0:
            return RiskDecision(
                order=None,
                rejection_reason=(
                    f"sized quantity for {signal.symbol} rounded to zero "
                    f"(equity={equity}, price={price})"
                ),
            )

        affordable_quantity = self._affordable_quantity(quantity, price, rules)
        if affordable_quantity <= 0:
            return RiskDecision(
                order=None,
                rejection_reason=(
                    f"insufficient cash for {signal.symbol}: available cash="
                    f"{self._portfolio.cash} cannot cover even the minimum fillable "
                    f"quantity at price={price} after fees/safety buffer"
                ),
            )

        return RiskDecision(
            order=self._build_order(signal, bar, OrderSide.BUY, affordable_quantity),
            rejection_reason=None,
        )

    def _affordable_quantity(
        self, quantity: Decimal, price: Decimal, rules: InstrumentRules
    ) -> Decimal:
        """Shrinks `quantity` (never increases it) so its worst-case cost never
        exceeds available cash.

        `EquityFractionSizer.size` sizes against *equity* at the signal bar's
        close — but the real fill lands on a *later* bar (see `LatencyModel`)
        at a worse price (`FillSimulator` applies spread) and always pays a
        fee. Sizing 100% of equity at the signal price alone can therefore
        leave `BacktestLedger.apply_fill` short of cash once the real,
        slightly-more-expensive fill lands (the cash-sufficiency gap
        documented in `docs/architecture.md`). Padding the reference price by
        `rules.taker_fee_rate` (worst-case fee) plus `cash_safety_buffer_pct`
        (a margin for spread/slippage) closes that gap without needing this
        engine to depend on `backtesting`'s fill-simulation models directly.
        """
        worst_case_unit_cost = price * (1 + self._cash_safety_buffer_pct + rules.taker_fee_rate)
        if worst_case_unit_cost <= 0:
            return Decimal("0")
        max_affordable = self._portfolio.cash / worst_case_unit_cost
        return round_qty(min(quantity, max_affordable), rules)

    def _evaluate_close(self, signal: Signal, bar: Bar, position: Position | None) -> RiskDecision:
        if position is None or position.is_flat:
            return RiskDecision(
                order=None,
                rejection_reason=f"no open position for {signal.symbol} to close",
            )

        return RiskDecision(
            order=self._build_order(signal, bar, OrderSide.SELL, position.quantity),
            rejection_reason=None,
        )

    @staticmethod
    def _build_order(signal: Signal, bar: Bar, side: OrderSide, quantity: Decimal) -> Order:
        return Order(
            order_id=uuid.uuid4().hex,
            correlation_id=_PENDING_CORRELATION_ID,
            symbol=signal.symbol,
            side=side,
            order_type=OrderType.MARKET,
            quantity=quantity,
            price=None,
            strategy_name=signal.strategy_name,
            created_at=bar.timestamp,
        )
