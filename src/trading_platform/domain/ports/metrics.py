from __future__ import annotations

from typing import Protocol


class IMetricsCollector(Protocol):
    """Thin abstraction over a metrics backend (Prometheus in production, an
    in-memory fake in unit tests) so application code never imports
    `prometheus_client` directly.

    Label dicts should use a small, fixed set of keys per metric name — avoid
    unbounded label cardinality (e.g. never label by `order_id` or `correlation_id`).
    """

    def increment_counter(
        self, name: str, labels: dict[str, str] | None = None, value: float = 1.0
    ) -> None: ...

    def observe_histogram(
        self, name: str, value: float, labels: dict[str, str] | None = None
    ) -> None: ...

    def set_gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None: ...
