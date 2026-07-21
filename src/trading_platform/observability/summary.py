from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from collections.abc import Sequence

from trading_platform.domain.ports.metrics import IMetricsCollector

logger = logging.getLogger(__name__)

_LATENCY_SAMPLE_LIMIT = 2000


def _percentile(samples: Sequence[float], percentile: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (percentile / 100)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


class SummaryTrackingMetricsCollector:
    """Decorates a real `IMetricsCollector` (Prometheus) and mirrors every call
    into small in-process rolling windows, so `PeriodicSummaryLogger` can emit
    human-readable rates/latency percentiles without reading Prometheus
    internals or requiring a running Prometheus server to query.

    The wrapped collector remains the sole source of truth for `/metrics`;
    this class only adds a side-channel for the periodic log summary.
    """

    def __init__(self, inner: IMetricsCollector) -> None:
        self._inner = inner
        self._lock = threading.Lock()
        self._counts: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = {}
        self._latencies: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=_LATENCY_SAMPLE_LIMIT)
        )

    def increment_counter(
        self, name: str, labels: dict[str, str] | None = None, value: float = 1.0
    ) -> None:
        self._inner.increment_counter(name, labels, value)
        with self._lock:
            self._counts[name] += value

    def observe_histogram(
        self, name: str, value: float, labels: dict[str, str] | None = None
    ) -> None:
        self._inner.observe_histogram(name, value, labels)
        key = self._keyed(name, labels)
        with self._lock:
            self._latencies[key].append(value)

    def set_gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        self._inner.set_gauge(name, value, labels)
        with self._lock:
            self._gauges[self._keyed(name, labels)] = value

    @staticmethod
    def _keyed(name: str, labels: dict[str, str] | None) -> str:
        handler = (labels or {}).get("handler")
        return f"{name}:{handler}" if handler else name

    def snapshot_and_reset_counts(self) -> dict[str, float]:
        with self._lock:
            counts = dict(self._counts)
            self._counts.clear()
        return counts

    def snapshot_gauges(self) -> dict[str, float]:
        with self._lock:
            return dict(self._gauges)

    def snapshot_latency_percentile_seconds(self, percentile: float = 99.0) -> dict[str, float]:
        with self._lock:
            samples_by_key = {key: list(values) for key, values in self._latencies.items()}
        return {key: _percentile(samples, percentile) for key, samples in samples_by_key.items()}


class PeriodicSummaryLogger:
    """Emits a structured INFO log with derived rates and latency percentiles
    every `interval_seconds`, matching the shape documented in
    `docs/architecture.md` (bars_per_sec, signals_per_sec, ..., cpu_percent).

    `maybe_emit()` is designed to be called frequently (e.g. once per second)
    from the application's main loop; it is a no-op until `interval_seconds`
    has elapsed since the last emission.
    """

    def __init__(
        self, tracker: SummaryTrackingMetricsCollector, interval_seconds: float = 60.0
    ) -> None:
        self._tracker = tracker
        self._interval_seconds = interval_seconds
        self._last_emit = time.monotonic()

    def maybe_emit(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_emit
        if elapsed < self._interval_seconds:
            return
        self.emit(elapsed)
        self._last_emit = now

    def emit(self, elapsed_seconds: float) -> None:
        counts = self._tracker.snapshot_and_reset_counts()
        gauges = self._tracker.snapshot_gauges()
        latencies_ms = {
            key: seconds * 1000
            for key, seconds in self._tracker.snapshot_latency_percentile_seconds().items()
        }

        def rate(metric_name: str) -> float:
            return counts.get(metric_name, 0.0) / elapsed_seconds if elapsed_seconds > 0 else 0.0

        def latency(handler_name: str) -> float:
            return latencies_ms.get(f"trading_handler_duration_seconds:{handler_name}", 0.0)

        summary = {
            "bars_per_sec": round(rate("trading_bars_processed_total"), 4),
            "signals_per_sec": round(rate("trading_signals_generated_total"), 4),
            "orders_per_sec": round(rate("trading_orders_submitted_total"), 4),
            "strategy_latency_p99_ms": round(latency("strategy"), 4),
            "risk_latency_p99_ms": round(latency("risk"), 4),
            "execution_latency_p99_ms": round(latency("execution"), 4),
            "memory_rss_mb": round(gauges.get("trading_memory_rss_bytes", 0.0) / (1024 * 1024), 2),
            "cpu_percent": round(gauges.get("trading_cpu_percent", 0.0), 2),
        }
        logger.info("metrics_summary", extra=summary)
