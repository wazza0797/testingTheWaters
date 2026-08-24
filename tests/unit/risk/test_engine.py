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
    """

    def __init__(self, equity: Decimal, position: Position | None = None) -> None:
        self._equity = equity
        self._position = position

    def position_for(self, symbol: str) -> Position | None:
        return self._position if self._position and self._position.symbol == symbol else None

    def equity(self, mark_prices: Mapping[str, Decimal]) -> Decimal:
        return self._equity


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
) -> PassThroughRiskEngine:
    return PassThroughRiskEngine(
        portfolio=StubPortfolio(equity, position),
        instrument_rules={"BTC/USDT": rules} if rules else {},
        sizer=EquityFractionSizer(fraction),
        pending_orders=StubPendingOrderTracker(pending_symbols),
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
        assert order.quantity == Decimal("0.2")
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
