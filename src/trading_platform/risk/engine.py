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
from trading_platform.domain.ports.risk import RiskDecision
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
      own — see `SignalGenerated.bar`).
    - `SELL`/`CLOSE` while holding a position: closes the *entire* position
      (no partial-reduce policy exists yet).

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
    ) -> None:
        self._portfolio = portfolio
        self._instrument_rules = instrument_rules
        self._sizer = sizer

    def evaluate(self, signal: Signal, bar: Bar) -> RiskDecision:
        rules = self._instrument_rules.get(signal.symbol)
        if rules is None:
            return RiskDecision(
                order=None, rejection_reason=f"no instrument rules for {signal.symbol!r}"
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

        return RiskDecision(
            order=self._build_order(signal, bar, OrderSide.BUY, quantity), rejection_reason=None
        )

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
