from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from trading_platform.domain.errors import StrategyError
from trading_platform.domain.events.base import Event
from trading_platform.domain.events.market import BarClosed
from trading_platform.domain.events.strategy import SignalGenerated
from trading_platform.domain.events.system import Heartbeat
from trading_platform.domain.models.signal import Signal, SignalType
from trading_platform.infrastructure.event_bus.in_memory import InMemoryEventBus
from trading_platform.strategies.context import DefaultStrategyContext
from trading_platform.strategies.examples.sma_crossover import SmaCrossoverStrategy
from trading_platform.strategies.handler import StrategyHandler
from trading_platform.strategies.loader import describe_strategy

_NAME = "test-strategy"


class RecordingStrategy:
    """Test double implementing `IStrategy`: records every hook call and lets
    the test control exactly which signals `on_bar` returns.
    """

    def __init__(self) -> None:
        self.start_calls = 0
        self.stop_calls = 0
        self.on_bar_calls: list[object] = []
        self.next_signals: list[Signal] = []

    def on_start(self, ctx) -> None:
        self.start_calls += 1

    def on_bar(self, bar, ctx):
        self.on_bar_calls.append(bar)
        return self.next_signals

    def on_stop(self, ctx) -> None:
        self.stop_calls += 1


def _context() -> DefaultStrategyContext:
    return DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")


def _handler(
    strategy, event_bus, symbol: str = "BTC/USDT", timeframe: str = "1h", name: str = _NAME
):
    return StrategyHandler(strategy, _context(), event_bus, symbol, timeframe, name)


def _signal(symbol: str = "BTC/USDT", strategy_name: str = "whatever-the-strategy-set") -> Signal:
    return Signal(
        symbol=symbol,
        signal_type=SignalType.BUY,
        strategy_name=strategy_name,
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
    )


class TestStrategyHandler:
    def test_ignores_events_that_are_not_bar_closed(self, fake_event_bus) -> None:
        strategy = RecordingStrategy()
        handler = _handler(strategy, fake_event_bus)

        handler.handle(Heartbeat(mode="paper", uptime_seconds=1.0))

        assert strategy.on_bar_calls == []
        assert fake_event_bus.published == []

    def test_ignores_bars_for_a_different_symbol(self, make_bar, fake_event_bus) -> None:
        strategy = RecordingStrategy()
        handler = _handler(strategy, fake_event_bus)

        handler.handle(BarClosed(bar=make_bar(symbol="ETH/USDT"), mode="backtest"))

        assert strategy.on_bar_calls == []

    def test_ignores_bars_for_a_different_timeframe(self, make_bar, fake_event_bus) -> None:
        strategy = RecordingStrategy()
        handler = _handler(strategy, fake_event_bus)

        handler.handle(BarClosed(bar=make_bar(symbol="BTC/USDT", timeframe="4h"), mode="backtest"))

        assert strategy.on_bar_calls == []

    def test_calls_on_start_once_before_first_matching_bar(self, make_bar, fake_event_bus) -> None:
        strategy = RecordingStrategy()
        handler = _handler(strategy, fake_event_bus)

        handler.handle(BarClosed(bar=make_bar(), mode="backtest"))
        handler.handle(BarClosed(bar=make_bar(), mode="backtest"))

        assert strategy.start_calls == 1
        assert len(strategy.on_bar_calls) == 2

    def test_publishes_signal_generated_for_each_returned_signal(
        self, make_bar, fake_event_bus
    ) -> None:
        strategy = RecordingStrategy()
        strategy.next_signals = [_signal(), _signal()]
        handler = _handler(strategy, fake_event_bus)

        handler.handle(BarClosed(bar=make_bar(), mode="backtest"))

        published = [e for e in fake_event_bus.published if isinstance(e, SignalGenerated)]
        assert len(published) == 2
        assert all(e.signal.symbol == "BTC/USDT" for e in published)

    def test_publishes_nothing_when_strategy_returns_no_signals(
        self, make_bar, fake_event_bus
    ) -> None:
        strategy = RecordingStrategy()
        handler = _handler(strategy, fake_event_bus)

        handler.handle(BarClosed(bar=make_bar(), mode="backtest"))

        assert fake_event_bus.published == []

    def test_signal_generated_reuses_correlation_id_from_triggering_bar(
        self, make_bar, fake_event_bus
    ) -> None:
        strategy = RecordingStrategy()
        strategy.next_signals = [_signal()]
        handler = _handler(strategy, fake_event_bus)
        bar_closed = BarClosed(bar=make_bar(), mode="backtest", correlation_id="trace-123")

        handler.handle(bar_closed)

        published = fake_event_bus.published[0]
        assert isinstance(published, SignalGenerated)
        assert published.correlation_id == "trace-123"

    def test_stop_calls_on_stop_only_if_started(self, fake_event_bus) -> None:
        strategy = RecordingStrategy()
        handler = _handler(strategy, fake_event_bus)

        handler.stop()

        assert strategy.stop_calls == 0

    def test_stop_calls_on_stop_after_started(self, make_bar, fake_event_bus) -> None:
        strategy = RecordingStrategy()
        handler = _handler(strategy, fake_event_bus)
        handler.handle(BarClosed(bar=make_bar(), mode="backtest"))

        handler.stop()

        assert strategy.stop_calls == 1

    def test_stop_is_idempotent(self, make_bar, fake_event_bus) -> None:
        strategy = RecordingStrategy()
        handler = _handler(strategy, fake_event_bus)
        handler.handle(BarClosed(bar=make_bar(), mode="backtest"))

        handler.stop()
        handler.stop()

        assert strategy.stop_calls == 1


class TestStrategyIdentityStamping:
    """`StrategyHandler` is the single place identity gets assigned — every
    published signal carries the handler's own `name`, regardless of what
    the wrapped strategy set on `Signal.strategy_name`. This is what makes
    two instances of the *same* strategy class (different params/symbols)
    distinguishable everywhere downstream without any strategy author
    needing to plumb a correct, unique name through themselves.
    """

    def test_published_signal_strategy_name_is_overwritten_with_handler_name(
        self, make_bar, fake_event_bus
    ) -> None:
        strategy = RecordingStrategy()
        strategy.next_signals = [_signal(strategy_name="whatever-the-strategy-set")]
        handler = _handler(
            strategy,
            fake_event_bus,
            name="SmaCrossoverStrategy[BTC/USDT](fast_period=5,slow_period=20)",
        )

        handler.handle(BarClosed(bar=make_bar(), mode="backtest"))

        published = fake_event_bus.published[0]
        assert isinstance(published, SignalGenerated)
        assert (
            published.signal.strategy_name
            == "SmaCrossoverStrategy[BTC/USDT](fast_period=5,slow_period=20)"
        )

    def test_two_handlers_for_the_same_strategy_class_get_distinct_identities(
        self, make_bar
    ) -> None:
        bus_fast, bus_slow = InMemoryEventBus(), InMemoryEventBus()
        recorder_fast, recorder_slow = _RecordingHandler(), _RecordingHandler()
        bus_fast.subscribe(SignalGenerated, recorder_fast)
        bus_slow.subscribe(SignalGenerated, recorder_slow)

        fast_strategy = RecordingStrategy()
        fast_strategy.next_signals = [_signal()]
        slow_strategy = RecordingStrategy()
        slow_strategy.next_signals = [_signal()]

        handler_fast = _handler(
            fast_strategy,
            bus_fast,
            name="SmaCrossoverStrategy[BTC/USDT](fast_period=5,slow_period=20)",
        )
        handler_slow = _handler(
            slow_strategy,
            bus_slow,
            name="SmaCrossoverStrategy[BTC/USDT](fast_period=20,slow_period=60)",
        )

        bar = BarClosed(bar=make_bar(), mode="backtest")
        handler_fast.handle(bar)
        handler_slow.handle(bar)

        assert (
            recorder_fast.received[0].signal.strategy_name
            == "SmaCrossoverStrategy[BTC/USDT](fast_period=5,slow_period=20)"
        )
        assert (
            recorder_slow.received[0].signal.strategy_name
            == "SmaCrossoverStrategy[BTC/USDT](fast_period=20,slow_period=60)"
        )


class TestSignalSymbolValidation:
    def test_raises_when_a_returned_signal_has_a_different_symbol_than_the_bar(
        self, make_bar, fake_event_bus
    ) -> None:
        strategy = RecordingStrategy()
        strategy.next_signals = [_signal(symbol="ETH/USDT")]
        handler = _handler(strategy, fake_event_bus)

        with pytest.raises(StrategyError, match="mismatched signal"):
            handler.handle(BarClosed(bar=make_bar(symbol="BTC/USDT"), mode="backtest"))

    def test_publishes_nothing_when_any_signal_in_the_batch_is_mismatched(
        self, make_bar, fake_event_bus
    ) -> None:
        strategy = RecordingStrategy()
        strategy.next_signals = [_signal(symbol="BTC/USDT"), _signal(symbol="ETH/USDT")]
        handler = _handler(strategy, fake_event_bus)

        with pytest.raises(StrategyError):
            handler.handle(BarClosed(bar=make_bar(symbol="BTC/USDT"), mode="backtest"))

        assert fake_event_bus.published == []


class _RecordingHandler:
    """Minimal `IEventHandler` that just remembers every event it's given."""

    def __init__(self) -> None:
        self.received: list[Event] = []

    def handle(self, event: Event) -> None:
        self.received.append(event)


class TestStrategyHandlerOnARealEventBus:
    """End-to-end through `InMemoryEventBus` (not just `handler.handle(...)`
    directly): publish synthetic `BarClosed` events and assert the resulting
    `SignalGenerated` events actually come out the other side of the bus.
    """

    def test_bar_closed_published_on_bus_yields_signal_generated_on_bus(self, make_bar) -> None:
        bus = InMemoryEventBus()
        strategy = RecordingStrategy()
        strategy.next_signals = [_signal()]
        handler = _handler(strategy, bus)
        bus.subscribe(BarClosed, handler)

        recorder = _RecordingHandler()
        bus.subscribe(SignalGenerated, recorder)

        bus.publish(BarClosed(bar=make_bar(), mode="backtest"))

        assert len(recorder.received) == 1
        received = recorder.received[0]
        assert isinstance(received, SignalGenerated)
        assert received.signal.symbol == "BTC/USDT"

    def test_real_sma_crossover_strategy_through_the_bus_end_to_end(self, make_bar) -> None:
        bus = InMemoryEventBus()
        ctx = DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")
        strategy = SmaCrossoverStrategy(fast_period=2, slow_period=3)
        # The identity a real caller would compute via describe_strategy(),
        # proving the loader's naming and the handler's stamping agree.
        name = describe_strategy(
            "trading_platform.strategies.examples.sma_crossover:SmaCrossoverStrategy",
            symbol="BTC/USDT",
            params={"fast_period": 2, "slow_period": 3},
        )
        handler = StrategyHandler(strategy, ctx, bus, "BTC/USDT", "1h", name)
        bus.subscribe(BarClosed, handler)

        recorder = _RecordingHandler()
        bus.subscribe(SignalGenerated, recorder)

        closes = ["100", "100", "100", "100", "100", "200", "50", "50", "50", "50", "50"]
        start = datetime(2024, 1, 1, tzinfo=UTC)
        for i, close in enumerate(closes):
            bar = make_bar(
                timestamp=start + timedelta(hours=i),
                open_=close,
                high=close,
                low=close,
                close=close,
            )
            bus.publish(BarClosed(bar=bar, mode="backtest"))

        signal_types = [e.signal.signal_type for e in recorder.received]
        assert signal_types == [SignalType.BUY, SignalType.SELL]
        assert all(e.signal.strategy_name == name for e in recorder.received)
