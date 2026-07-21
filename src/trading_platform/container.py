from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI

from trading_platform.config.loader import AppConfig
from trading_platform.config.settings import Settings
from trading_platform.domain.events.execution import FillReceived, OrderRejected
from trading_platform.domain.events.market import BarClosed
from trading_platform.domain.events.risk import OrderApproved, RiskRejected
from trading_platform.domain.events.strategy import SignalGenerated
from trading_platform.domain.events.system import Heartbeat
from trading_platform.domain.ports.event_bus import IEventBus
from trading_platform.infrastructure.event_bus.in_memory import InMemoryEventBus
from trading_platform.infrastructure.event_bus.timed import TimedEventBus
from trading_platform.infrastructure.metrics.prometheus import PrometheusMetricsCollector
from trading_platform.observability.handler import MetricsHandler
from trading_platform.observability.server import HealthStatus, create_app
from trading_platform.observability.summary import (
    PeriodicSummaryLogger,
    SummaryTrackingMetricsCollector,
)
from trading_platform.observability.system_monitor import SystemMonitor

# Every event type MetricsHandler translates into a throughput counter (see
# docs/architecture.md metric catalog). Strategy/risk/execution/notification
# handlers are added to this wiring in their respective milestones (M3/M4/M6/M7)
# — nothing here should need to change when they land.
#
# Heartbeat is included even though MetricsHandler doesn't count it: it gives
# the M0 skeleton (which has no strategy/risk/execution handlers yet) at least
# one real handler invocation to exercise the TimedEventBus latency pipeline
# end-to-end (see `main.py`'s background loop and the M0 acceptance criteria).
_METRICS_HANDLER_EVENT_TYPES = (
    BarClosed,
    SignalGenerated,
    OrderApproved,
    RiskRejected,
    OrderRejected,
    FillReceived,
    Heartbeat,
)


@dataclass
class AppContainer:
    """Composition root: owns every long-lived singleton and wires event bus
    subscriptions. Nothing outside this module should construct handlers or
    the event bus directly (see coding standards: "register subscriptions
    only in container.py").
    """

    settings: Settings
    config: AppConfig
    event_bus: IEventBus
    prometheus_collector: PrometheusMetricsCollector
    system_monitor: SystemMonitor
    summary_logger: PeriodicSummaryLogger
    health: HealthStatus

    def observability_app(self) -> FastAPI:
        return create_app(self.prometheus_collector, self.health)


def build_container(settings: Settings, config: AppConfig) -> AppContainer:
    prometheus_collector = PrometheusMetricsCollector()
    tracked_metrics = SummaryTrackingMetricsCollector(prometheus_collector)

    inner_bus = InMemoryEventBus()
    event_bus = TimedEventBus(inner_bus, tracked_metrics)

    metrics_handler = MetricsHandler(tracked_metrics)
    for event_type in _METRICS_HANDLER_EVENT_TYPES:
        event_bus.subscribe(event_type, metrics_handler)

    system_monitor = SystemMonitor(tracked_metrics)
    summary_logger = PeriodicSummaryLogger(
        tracked_metrics, interval_seconds=config.observability.log_summary_interval_sec
    )
    health = HealthStatus()

    return AppContainer(
        settings=settings,
        config=config,
        event_bus=event_bus,
        prometheus_collector=prometheus_collector,
        system_monitor=system_monitor,
        summary_logger=summary_logger,
        health=health,
    )
