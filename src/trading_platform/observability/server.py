"""HTTP surfaces for process health and Prometheus metrics.

Health and metrics are separate apps so operators can publish `/health` on a
host port without also exposing unauthenticated `/metrics` (M9).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST

from trading_platform.infrastructure.metrics.prometheus import PrometheusMetricsCollector


@dataclass
class HealthStatus:
    """Mutable, injectable health state.

    M0 only tracks process uptime. M6+ extends this with feed liveness (last
    bar timestamp, feed status) without changing the `/health` route itself.
    """

    started_at: float = field(default_factory=time.monotonic)

    @property
    def uptime_seconds(self) -> float:
        return time.monotonic() - self.started_at

    def as_dict(self) -> dict[str, object]:
        return {"status": "ok", "uptime_seconds": round(self.uptime_seconds, 3)}


def create_health_app(health: HealthStatus) -> FastAPI:
    """`GET /health` only — safe to publish on a host port for probes."""
    app = FastAPI(title="trading-platform health", docs_url=None, redoc_url=None)

    @app.get("/health")
    def get_health() -> dict[str, object]:
        return health.as_dict()

    return app


def create_metrics_app(metrics: PrometheusMetricsCollector) -> FastAPI:
    """`GET /metrics` only — keep on the Docker network / localhost by default."""
    app = FastAPI(title="trading-platform metrics", docs_url=None, redoc_url=None)

    @app.get("/metrics")
    def get_metrics() -> Response:
        return Response(content=metrics.render_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


def create_app(metrics: PrometheusMetricsCollector, health: HealthStatus) -> FastAPI:
    """Combined app (both routes) for tests and same-port local debugging."""
    app = FastAPI(title="trading-platform observability", docs_url=None, redoc_url=None)

    @app.get("/health")
    def get_health() -> dict[str, object]:
        return health.as_dict()

    @app.get("/metrics")
    def get_metrics() -> Response:
        return Response(content=metrics.render_latest(), media_type=CONTENT_TYPE_LATEST)

    return app
