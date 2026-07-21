from __future__ import annotations

from fastapi.testclient import TestClient

from trading_platform.infrastructure.metrics.prometheus import PrometheusMetricsCollector
from trading_platform.observability.server import HealthStatus, create_app


class TestObservabilityServer:
    def test_health_endpoint_returns_ok_status(self) -> None:
        app = create_app(PrometheusMetricsCollector(), HealthStatus())
        client = TestClient(app)

        response = client.get("/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["uptime_seconds"] >= 0

    def test_metrics_endpoint_returns_prometheus_text_format(self) -> None:
        collector = PrometheusMetricsCollector()
        collector.set_gauge("trading_process_uptime_seconds", 42.0)
        app = create_app(collector, HealthStatus())
        client = TestClient(app)

        response = client.get("/metrics")

        assert response.status_code == 200
        assert "trading_process_uptime_seconds 42.0" in response.text

    def test_metrics_endpoint_reflects_collector_state(self) -> None:
        collector = PrometheusMetricsCollector()
        app = create_app(collector, HealthStatus())
        client = TestClient(app)
        collector.increment_counter(
            "trading_bars_processed_total", labels={"mode": "backtest", "symbol": "BTC/USDT"}
        )

        response = client.get("/metrics")

        assert "trading_bars_processed_total" in response.text
