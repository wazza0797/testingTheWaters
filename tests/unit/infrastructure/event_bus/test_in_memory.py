from __future__ import annotations

from dataclasses import dataclass

import pytest

from trading_platform.domain.events.base import Event
from trading_platform.infrastructure.event_bus.in_memory import InMemoryEventBus


@dataclass(frozen=True, slots=True, kw_only=True)
class _DummyEvent(Event):
    payload: str = "default"


@dataclass(frozen=True, slots=True, kw_only=True)
class _OtherEvent(Event):
    pass


class _RecordingHandler:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self.calls = calls

    def handle(self, event: Event) -> None:
        self.calls.append(self.name)


class _RaisingHandler:
    name = "raiser"

    def handle(self, event: Event) -> None:
        raise RuntimeError("handler exploded")


class TestInMemoryEventBus:
    def test_publish_invokes_subscribed_handler(self) -> None:
        bus = InMemoryEventBus()
        calls: list[str] = []
        handler = _RecordingHandler("h1", calls)
        bus.subscribe(_DummyEvent, handler)

        bus.publish(_DummyEvent())

        assert calls == ["h1"]

    def test_handlers_run_in_registration_order(self) -> None:
        bus = InMemoryEventBus()
        calls: list[str] = []
        bus.subscribe(_DummyEvent, _RecordingHandler("first", calls))
        bus.subscribe(_DummyEvent, _RecordingHandler("second", calls))

        bus.publish(_DummyEvent())

        assert calls == ["first", "second"]

    def test_handler_only_receives_subscribed_event_type(self) -> None:
        bus = InMemoryEventBus()
        calls: list[str] = []
        bus.subscribe(_DummyEvent, _RecordingHandler("h1", calls))

        bus.publish(_OtherEvent())

        assert calls == []

    def test_multiple_handlers_for_same_event_type(self) -> None:
        bus = InMemoryEventBus()
        calls: list[str] = []
        bus.subscribe(_DummyEvent, _RecordingHandler("a", calls))
        bus.subscribe(_DummyEvent, _RecordingHandler("b", calls))

        bus.publish(_DummyEvent())

        assert set(calls) == {"a", "b"}

    def test_unsubscribe_stops_future_delivery(self) -> None:
        bus = InMemoryEventBus()
        calls: list[str] = []
        handler = _RecordingHandler("h1", calls)
        bus.subscribe(_DummyEvent, handler)
        bus.unsubscribe(_DummyEvent, handler)

        bus.publish(_DummyEvent())

        assert calls == []

    def test_duplicate_subscribe_does_not_double_invoke(self) -> None:
        bus = InMemoryEventBus()
        calls: list[str] = []
        handler = _RecordingHandler("h1", calls)
        bus.subscribe(_DummyEvent, handler)
        bus.subscribe(_DummyEvent, handler)

        bus.publish(_DummyEvent())

        assert calls == ["h1"]

    def test_unsubscribe_unknown_handler_is_a_noop(self) -> None:
        bus = InMemoryEventBus()
        handler = _RecordingHandler("h1", [])
        bus.unsubscribe(_DummyEvent, handler)  # should not raise

    def test_handler_exception_propagates_to_publisher(self) -> None:
        bus = InMemoryEventBus()
        bus.subscribe(_DummyEvent, _RaisingHandler())

        with pytest.raises(RuntimeError, match="handler exploded"):
            bus.publish(_DummyEvent())

    def test_publish_with_no_subscribers_does_not_raise(self) -> None:
        bus = InMemoryEventBus()
        bus.publish(_DummyEvent())
