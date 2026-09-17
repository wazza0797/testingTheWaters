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

    Position semantics (one open position per symbol, no pyramiding):

    - `BUY` while flat → open long (sized + cash-affordable).
    - `BUY` while short → cover the entire short (`OrderSide.BUY`).
    - `BUY` while long → rejected.
    - `SELL` while flat → open short **only if** `InstrumentRules.allows_short`
      (derivatives/CFDs); spot stays False so SELL-while-flat is rejected.
    - `SELL` while long → close the entire long.
    - `SELL` while short → rejected (no short pyramiding).
    - `CLOSE` while long → sell to flat; while short → buy to cover; flat → reject.

    - **Any** signal while an earlier order for the same symbol is still
      outstanding (queued on latency, or only partially filled): rejected.
      `IPortfolioView.position_for` only reflects *filled* fills, so without
      this check a second entry could be approved before the first fill
      lands. See `IPendingOrderTracker`.

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
        fill_cost_fraction: float = 0.0,
    ) -> None:
        self._portfolio = portfolio
        self._instrument_rules = instrument_rules
        self._sizer = sizer
        self._pending_orders = pending_orders
        self._cash_safety_buffer_pct = Decimal(str(cash_safety_buffer_pct))
        # Worst-case half-spread as a fraction of price (flat spread_bps plus
        # any volatility headroom). Must match SpreadModel.max_half_spread_fraction
        # so a vol-widened fill cannot overdraw cash after approval.
        self._fill_cost_fraction = Decimal(str(fill_cost_fraction))

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
        if signal.signal_type == SignalType.SELL:
            return self._evaluate_sell(signal, bar, rules, position)
        if signal.signal_type == SignalType.CLOSE:
            return self._evaluate_close(signal, bar, position)
        return RiskDecision(
            order=None, rejection_reason=f"unsupported signal_type {signal.signal_type!r}"
        )

    def _evaluate_buy(
        self, signal: Signal, bar: Bar, rules: InstrumentRules, position: Position | None
    ) -> RiskDecision:
        if position is not None and position.quantity < 0:
            # Cover short.
            return RiskDecision(
                order=self._build_order(signal, bar, OrderSide.BUY, abs(position.quantity)),
                rejection_reason=None,
            )
        if position is not None and not position.is_flat:
            return RiskDecision(
                order=None,
                rejection_reason=(
                    f"already in a long position for {signal.symbol} "
                    f"(qty={position.quantity}); ignoring BUY signal"
                ),
            )
        return self._evaluate_open(signal, bar, rules, OrderSide.BUY)

    def _evaluate_sell(
        self, signal: Signal, bar: Bar, rules: InstrumentRules, position: Position | None
    ) -> RiskDecision:
        if position is not None and position.quantity > 0:
            # Close long.
            return RiskDecision(
                order=self._build_order(signal, bar, OrderSide.SELL, position.quantity),
                rejection_reason=None,
            )
        if position is not None and position.quantity < 0:
            return RiskDecision(
                order=None,
                rejection_reason=(
                    f"already in a short position for {signal.symbol} "
                    f"(qty={position.quantity}); ignoring SELL signal"
                ),
            )
        if not rules.allows_short:
            return RiskDecision(
                order=None,
                rejection_reason=(
                    f"shorts not allowed for {signal.symbol} "
                    f"(allows_short=False — spot/long-only instrument); "
                    f"ignoring SELL-while-flat"
                ),
            )
        return self._evaluate_open(signal, bar, rules, OrderSide.SELL)

    def _evaluate_open(
        self, signal: Signal, bar: Bar, rules: InstrumentRules, side: OrderSide
    ) -> RiskDecision:
        price = bar.close
        equity = self._portfolio.equity({signal.symbol: price})
        quantity = self._size_open(signal, equity, price, rules)
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
            order=self._build_order(signal, bar, side, affordable_quantity),
            rejection_reason=None,
        )

    def _size_open(
        self,
        signal: Signal,
        equity: Decimal,
        price: Decimal,
        rules: InstrumentRules,
    ) -> Decimal:
        """Size an opening order.

        Default: `EquityFractionSizer` (fraction of equity).

        When `signal.metadata["sizing"] == "atr_risk"`, size so that
        `risk_pct * equity` equals `atr_stop_mult * atr` dollars of stop
        distance (Connors-style). ATR/risk fields must be present and
        positive; otherwise falls back to the equity-fraction sizer.
        """
        meta = signal.metadata
        if meta.get("sizing") == "atr_risk":
            try:
                atr = Decimal(str(meta["atr"]))
                risk_pct = Decimal(str(meta["risk_pct"]))
                stop_mult = Decimal(str(meta.get("atr_stop_mult", 2.0)))
            except (KeyError, TypeError, ValueError, ArithmeticError):
                return self._sizer.size(equity, price, rules)
            if atr > 0 and risk_pct > 0 and stop_mult > 0 and equity > 0:
                stop_dist = stop_mult * atr
                raw = (equity * risk_pct) / stop_dist
                return round_qty(raw, rules)
        return self._sizer.size(equity, price, rules)

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
        slightly-more-expensive fill lands. Padding the reference price by
        `rules.taker_fee_rate`, `cash_safety_buffer_pct`, and
        `fill_cost_fraction` (worst-case half-spread from `SpreadModel`,
        including volatility headroom) closes that gap without this engine
        depending on fill-simulation internals.

        Used for both long opens and short opens: for shorts this is a
        conservative stand-in for margin until a real CFD margin model lands
        (same cash ceiling, not free leverage).
        """
        worst_case_unit_cost = price * (
            1 + self._cash_safety_buffer_pct + self._fill_cost_fraction + rules.taker_fee_rate
        )
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

        if position.quantity > 0:
            return RiskDecision(
                order=self._build_order(signal, bar, OrderSide.SELL, position.quantity),
                rejection_reason=None,
            )
        return RiskDecision(
            order=self._build_order(signal, bar, OrderSide.BUY, abs(position.quantity)),
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
