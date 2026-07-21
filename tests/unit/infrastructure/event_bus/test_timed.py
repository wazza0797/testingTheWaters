from __future__ import annotations

from dataclasses import dataclass

import pytest

from trading_platform.domain.events.base import Event
from trading_platform.infrastructure.event_bus.in_memory import InMemoryEventBus
from trading_platform.infrastructure.event_bus.timed import (
    EVENTS_PUBLISHED_METRIC,
    HANDLER_DURATION_METRIC,
    HANDLER_ERRORS_METRIC,
    TimedEventBus,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class _DummyEvent(Event):
    pass


class _NamedHandler:
    def __init__(self, name: str, *, raises: Exception | None = None) -> None:
        self.name = name
        self._raises = raises
        self.invocations = 0

    def handle(self, event: Event) -> None:
        self.invocations += 1
        if self._raises is not None:
            raise self._raises


class _UnnamedHandler:
    def handle(self, event: Event) -> None:
        pass


class TestTimedEventBus:
    def test_records_handler_duration_histogram(self, fake_metrics) -> None:
        bus = TimedEventBus(InMemoryEventBus(), fake_metrics)
        handler = _NamedHandler("strategy")
        bus.subscribe(_DummyEvent, handler)

        bus.publish(_DummyEvent())

        histogram_calls = [c for c in fake_metrics.histograms if c.name == HANDLER_DURATION_METRIC]
        assert len(histogram_calls) == 1
        assert histogram_calls[0].labels == {"handler": "strategy", "event_type": "_DummyEvent"}
        assert histogram_calls[0].value >= 0

    def test_records_events_published_counter(self, fake_metrics) -> None:
        bus = TimedEventBus(InMemoryEventBus(), fake_metrics)

        bus.publish(_DummyEvent())

        assert fake_metrics.counter_total(EVENTS_PUBLISHED_METRIC, event_type="_DummyEvent") == 1

    def test_handler_name_falls_back_to_class_name(self, fake_metrics) -> None:
        bus = TimedEventBus(InMemoryEventBus(), fake_metrics)
        bus.subscribe(_DummyEvent, _UnnamedHandler())

        bus.publish(_DummyEvent())

        histogram_calls = [c for c in fake_metrics.histograms if c.name == HANDLER_DURATION_METRIC]
        assert histogram_calls[0].labels["handler"] == "_UnnamedHandler"

    def test_handler_exception_increments_error_counter_and_reraises(self, fake_metrics) -> None:
        bus = TimedEventBus(InMemoryEventBus(), fake_metrics)
        handler = _NamedHandler("execution", raises=ValueError("bad order"))
        bus.subscribe(_DummyEvent, handler)

        with pytest.raises(ValueError, match="bad order"):
            bus.publish(_DummyEvent())

        error_calls = [c for c in fake_metrics.counters if c.name == HANDLER_ERRORS_METRIC]
        assert error_calls[0].labels == {"handler": "execution", "error_type": "ValueError"}

    def test_multiple_handlers_each_get_own_wrapper(self, fake_metrics) -> None:
        bus = TimedEventBus(InMemoryEventBus(), fake_metrics)
        first = _NamedHandler("strategy")
        second = _NamedHandler("risk")
        bus.subscribe(_DummyEvent, first)
        bus.subscribe(_DummyEvent, second)

        bus.publish(_DummyEvent())

        assert first.invocations == 1
        assert second.invocations == 1

    def test_unsubscribe_removes_wrapped_handler(self, fake_metrics) -> None:
        bus = TimedEventBus(InMemoryEventBus(), fake_metrics)
        handler = _NamedHandler("strategy")
        bus.subscribe(_DummyEvent, handler)
        bus.unsubscribe(_DummyEvent, handler)

        bus.publish(_DummyEvent())

        assert handler.invocations == 0
