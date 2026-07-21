from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest


class PrometheusMetricsCollector:
    """`IMetricsCollector` backed by `prometheus_client`.

    Prometheus metric objects must declare their label *names* upfront, while
    the `IMetricsCollector` port passes a label dict per call. This collector
    lazily creates (and caches) one Counter/Histogram/Gauge per unique
    (metric name, sorted label keys) pair the first time it's observed.

    Each metric name must always be called with the same set of label keys —
    calling it with a different label set later will raise, since Prometheus
    does not support redefining a metric's label names. The metric catalog in
    `docs/architecture.md` fixes the label set per metric name for this reason.
    """

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        self._counters: dict[tuple[str, tuple[str, ...]], Counter] = {}
        self._histograms: dict[tuple[str, tuple[str, ...]], Histogram] = {}
        self._gauges: dict[tuple[str, tuple[str, ...]], Gauge] = {}

    def increment_counter(
        self, name: str, labels: dict[str, str] | None = None, value: float = 1.0
    ) -> None:
        labels = labels or {}
        key = (name, tuple(sorted(labels)))
        counter = self._counters.get(key)
        if counter is None:
            counter = Counter(name, name, labelnames=sorted(labels), registry=self.registry)
            self._counters[key] = counter
        target = counter.labels(**labels) if labels else counter
        target.inc(value)

    def observe_histogram(
        self, name: str, value: float, labels: dict[str, str] | None = None
    ) -> None:
        labels = labels or {}
        key = (name, tuple(sorted(labels)))
        histogram = self._histograms.get(key)
        if histogram is None:
            histogram = Histogram(name, name, labelnames=sorted(labels), registry=self.registry)
            self._histograms[key] = histogram
        target = histogram.labels(**labels) if labels else histogram
        target.observe(value)

    def set_gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        labels = labels or {}
        key = (name, tuple(sorted(labels)))
        gauge = self._gauges.get(key)
        if gauge is None:
            gauge = Gauge(name, name, labelnames=sorted(labels), registry=self.registry)
            self._gauges[key] = gauge
        target = gauge.labels(**labels) if labels else gauge
        target.set(value)

    def render_latest(self) -> bytes:
        """Render all metrics in Prometheus text exposition format for `GET /metrics`."""
        return generate_latest(self.registry)
