from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal

from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.domain.models.order import OrderSide
from trading_platform.domain.models.position import Position
from trading_platform.domain.models.signal import Signal, SignalType
from trading_platform.risk.engine import PassThroughRiskEngine
from trading_platform.risk.sizing import EquityFractionSizer

UTC_TS = datetime(2024, 1, 1, tzinfo=UTC)


class StubPortfolio:
    """Minimal `IPortfolioView` test double — a fixed equity value and an
    optionally-preset position, independent of any real ledger implementation.

    `cash` defaults to the same value as `equity` (true whenever there's no
    open position to mark-to-market, which is the common case in these
    tests) unless a test explicitly needs them to diverge.
    """

    def __init__(
        self,
        equity: Decimal,
        position: Position | None = None,
        cash: Decimal | None = None,
    ) -> None:
        self._equity = equity
        self._position = position
        self._cash = cash if cash is not None else equity

    def position_for(self, symbol: str) -> Position | None:
        return self._position if self._position and self._position.symbol == symbol else None

    def equity(self, mark_prices: Mapping[str, Decimal]) -> Decimal:
        return self._equity

    @property
    def cash(self) -> Decimal:
        return self._cash


class StubPendingOrderTracker:
    """Minimal `IPendingOrderTracker` test double — a fixed, symbol-agnostic
    answer unless a specific symbol is marked pending.
    """

    def __init__(self, pending_symbols: frozenset[str] = frozenset()) -> None:
        self._pending_symbols = pending_symbols

    def has_pending_order(self, symbol: str) -> bool:
        return symbol in self._pending_symbols


def _signal(signal_type: SignalType, symbol: str = "BTC/USDT") -> Signal:
    return Signal(
        symbol=symbol, signal_type=signal_type, strategy_name="test-strategy", timestamp=UTC_TS
    )


def _engine(
    equity: Decimal = Decimal("10000"),
    position: Position | None = None,
    rules: InstrumentRules | None = None,
    fraction: float = 1.0,
    pending_symbols: frozenset[str] = frozenset(),
    cash: Decimal | None = None,
    cash_safety_buffer_pct: float = 0.001,
) -> PassThroughRiskEngine:
    return PassThroughRiskEngine(
        portfolio=StubPortfolio(equity, position, cash),
        instrument_rules={"BTC/USDT": rules} if rules else {},
        sizer=EquityFractionSizer(fraction),
        pending_orders=StubPendingOrderTracker(pending_symbols),
        cash_safety_buffer_pct=cash_safety_buffer_pct,
    )


class TestUnknownInstrument:
    def test_rejects_when_no_instrument_rules_registered(self, make_bar) -> None:
        engine = _engine(rules=None)

        decision = engine.evaluate(_signal(SignalType.BUY), make_bar())

        assert not decision.approved
        assert decision.rejection_reason is not None
        assert "instrument rules" in decision.rejection_reason


class TestBuySignal:
    def test_approves_and_sizes_a_buy_when_flat(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        engine = _engine(equity=Decimal("10000"), rules=btc_usdt_instrument_rules)
        bar = make_bar(close="50000", open_="50000", high="50000", low="50000")

        decision = engine.evaluate(_signal(SignalType.BUY), bar)

        assert decision.approved
        order = decision.order
        assert order is not None
        assert order.side == OrderSide.BUY
        assert order.symbol == "BTC/USDT"
        # Sizer alone would size 0.2 (100% of 10000 equity / 50000 price), but
        # the cash-sufficiency guard trims it slightly so its worst-case cost
        # (price * (1 + taker_fee_rate 0.001 + cash_safety_buffer_pct 0.001))
        # never exceeds the 10000 cash actually available. See
        # `PassThroughRiskEngine._affordable_quantity`.
        assert order.quantity == Decimal("0.19960")
        assert order.price is None  # market order
        assert order.strategy_name == "test-strategy"

    def test_rejects_a_buy_when_already_long(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        position = Position(
            symbol="BTC/USDT", quantity=Decimal("1"), average_entry_price=Decimal("100")
        )
        engine = _engine(position=position, rules=btc_usdt_instrument_rules)

        decision = engine.evaluate(_signal(SignalType.BUY), make_bar())

        assert not decision.approved
        assert "already in a position" in (decision.rejection_reason or "")

    def test_rejects_a_buy_that_sizes_to_zero(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        engine = _engine(equity=Decimal("0"), rules=btc_usdt_instrument_rules)

        decision = engine.evaluate(_signal(SignalType.BUY), make_bar())

        assert not decision.approved
        assert "rounded to zero" in (decision.rejection_reason or "")

    def test_buy_order_quantity_respects_position_size_pct(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        engine = _engine(equity=Decimal("10000"), rules=btc_usdt_instrument_rules, fraction=0.5)
        bar = make_bar(close="50000", open_="50000", high="50000", low="50000")

        decision = engine.evaluate(_signal(SignalType.BUY), bar)

        assert decision.order is not None
        assert decision.order.quantity == Decimal("0.1")


class TestCashSufficiencyGuard:
    """Regression coverage for the documented cash-sufficiency gap
    (docs/architecture.md): `EquityFractionSizer` sizes against *equity* at
    the signal bar's close, but the real fill lands later, at a
    spread-adjusted price, and always pays a fee — so 100%-of-equity sizing
    could otherwise leave `BacktestLedger.apply_fill` short of cash.
    """

    def test_full_equity_sizing_is_trimmed_to_fit_available_cash(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        # equity == cash (flat, no other positions): sizer alone would spend
        # every last dollar (0.2 BTC @ 50000), leaving nothing for the fee.
        engine = _engine(
            equity=Decimal("10000"), cash=Decimal("10000"), rules=btc_usdt_instrument_rules
        )
        bar = make_bar(close="50000", open_="50000", high="50000", low="50000")

        decision = engine.evaluate(_signal(SignalType.BUY), bar)

        assert decision.approved
        order = decision.order
        assert order is not None
        assert order.quantity < Decimal("0.2")
        # Total worst-case cost must actually fit within cash.
        worst_case_cost = order.quantity * Decimal("50000") * Decimal("1.002")
        assert worst_case_cost <= Decimal("10000")

    def test_sizing_untouched_when_cash_comfortably_exceeds_sized_cost(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        # Sizer only wants 50% of equity — plenty of headroom even though
        # cash == equity, so the guard shouldn't trim anything.
        engine = _engine(
            equity=Decimal("10000"),
            cash=Decimal("10000"),
            rules=btc_usdt_instrument_rules,
            fraction=0.5,
        )
        bar = make_bar(close="50000", open_="50000", high="50000", low="50000")

        decision = engine.evaluate(_signal(SignalType.BUY), bar)

        assert decision.approved
        assert decision.order is not None
        assert decision.order.quantity == Decimal("0.1")

    def test_rejects_a_buy_when_cash_is_essentially_exhausted(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        # Equity (for sizing) is healthy, but actual cash on hand is far
        # below what min_qty (0.00001 BTC @ 50000 ~= $0.50) would cost —
        # e.g. most of "equity" is tied up in an open position elsewhere.
        engine = _engine(
            equity=Decimal("10000"), cash=Decimal("0.01"), rules=btc_usdt_instrument_rules
        )
        bar = make_bar(close="50000", open_="50000", high="50000", low="50000")

        decision = engine.evaluate(_signal(SignalType.BUY), bar)

        assert not decision.approved
        assert "insufficient cash" in (decision.rejection_reason or "")

    def test_zero_cash_safety_buffer_still_leaves_room_for_the_fee(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        # Even with the buffer knob dialed to zero, the known taker fee rate
        # alone still guards against overdrawing cash.
        engine = _engine(
            equity=Decimal("10000"),
            cash=Decimal("10000"),
            rules=btc_usdt_instrument_rules,
            cash_safety_buffer_pct=0.0,
        )
        bar = make_bar(close="50000", open_="50000", high="50000", low="50000")

        decision = engine.evaluate(_signal(SignalType.BUY), bar)

        assert decision.approved
        order = decision.order
        assert order is not None
        worst_case_cost = order.quantity * Decimal("50000") * Decimal("1.001")
        assert worst_case_cost <= Decimal("10000")


class TestSellAndCloseSignals:
    def test_rejects_a_sell_when_flat(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        engine = _engine(rules=btc_usdt_instrument_rules)

        decision = engine.evaluate(_signal(SignalType.SELL), make_bar())

        assert not decision.approved
        assert "no open position" in (decision.rejection_reason or "")

    def test_approves_a_sell_that_closes_the_full_position(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        position = Position(
            symbol="BTC/USDT", quantity=Decimal("0.2"), average_entry_price=Decimal("50000")
        )
        engine = _engine(position=position, rules=btc_usdt_instrument_rules)

        decision = engine.evaluate(_signal(SignalType.SELL), make_bar())

        assert decision.approved
        order = decision.order
        assert order is not None
        assert order.side == OrderSide.SELL
        assert order.quantity == Decimal("0.2")

    def test_close_signal_behaves_the_same_as_sell(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        position = Position(
            symbol="BTC/USDT", quantity=Decimal("0.2"), average_entry_price=Decimal("50000")
        )
        engine = _engine(position=position, rules=btc_usdt_instrument_rules)

        decision = engine.evaluate(_signal(SignalType.CLOSE), make_bar())

        assert decision.approved
        assert decision.order is not None
        assert decision.order.side == OrderSide.SELL
        assert decision.order.quantity == Decimal("0.2")


class TestPendingOrderGate:
    """Regression tests for a real bug caught in review: `position_for`
    alone only reflects *filled* fills, so without also consulting
    `IPendingOrderTracker`, a second signal could be approved while an
    earlier order for the same symbol is still outstanding (queued on
    latency, or only partially filled) — breaking the "no averaging/
    pyramiding, no double-close" policy this engine otherwise enforces.
    """

    def test_rejects_a_buy_while_flat_if_an_order_is_already_pending(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        # Flat per the ledger (no filled position yet), but an earlier BUY
        # for this symbol is still working its way through latency/partial
        # fills — approving a second one here would double the eventual
        # position once both land.
        engine = _engine(rules=btc_usdt_instrument_rules, pending_symbols=frozenset({"BTC/USDT"}))

        decision = engine.evaluate(_signal(SignalType.BUY), make_bar())

        assert not decision.approved
        assert "already pending" in (decision.rejection_reason or "")

    def test_rejects_a_sell_while_holding_if_an_order_is_already_pending(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        position = Position(
            symbol="BTC/USDT", quantity=Decimal("0.2"), average_entry_price=Decimal("50000")
        )
        engine = _engine(
            position=position,
            rules=btc_usdt_instrument_rules,
            pending_symbols=frozenset({"BTC/USDT"}),
        )

        decision = engine.evaluate(_signal(SignalType.SELL), make_bar())

        assert not decision.approved
        assert "already pending" in (decision.rejection_reason or "")

    def test_a_pending_order_for_a_different_symbol_does_not_block_this_one(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        engine = _engine(rules=btc_usdt_instrument_rules, pending_symbols=frozenset({"ETH/USDT"}))
        bar = make_bar(close="50000", open_="50000", high="50000", low="50000")

        decision = engine.evaluate(_signal(SignalType.BUY), bar)

        assert decision.approved

    def test_approves_normally_once_the_pending_order_clears(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        engine = _engine(rules=btc_usdt_instrument_rules, pending_symbols=frozenset())
        bar = make_bar(close="50000", open_="50000", high="50000", low="50000")

        decision = engine.evaluate(_signal(SignalType.BUY), bar)

        assert decision.approved


class TestOrderConstructionDetails:
    def test_order_created_at_uses_the_bars_timestamp(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        engine = _engine(rules=btc_usdt_instrument_rules)
        bar = make_bar(
            timestamp=datetime(2024, 3, 1, tzinfo=UTC),
            close="50000",
            open_="50000",
            high="50000",
            low="50000",
        )

        decision = engine.evaluate(_signal(SignalType.BUY), bar)

        assert decision.order is not None
        assert decision.order.created_at == datetime(2024, 3, 1, tzinfo=UTC)

    def test_two_buy_orders_get_distinct_order_ids(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        engine = _engine(rules=btc_usdt_instrument_rules)
        bar = make_bar(close="50000", open_="50000", high="50000", low="50000")

        first = engine.evaluate(_signal(SignalType.BUY), bar)
        second = engine.evaluate(_signal(SignalType.BUY), bar)

        assert first.order is not None
        assert second.order is not None
        assert first.order.order_id != second.order.order_id
