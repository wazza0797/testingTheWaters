from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from trading_platform.config.loader import load_config
from trading_platform.config.settings import Settings
from trading_platform.container import build_container
from trading_platform.domain.events.system import Heartbeat


class TestBuildContainer:
    def test_wires_event_bus_metrics_and_observability_app(self) -> None:
        settings = Settings(_env_file=None)
        config = load_config(config_dir=Path("config"))
        container = build_container(settings, config)

        container.event_bus.publish(Heartbeat(mode="test", uptime_seconds=1.0))
        container.system_monitor.poll_once()

        client = TestClient(container.observability_app())
        health_response = client.get("/health")
        metrics_response = client.get("/metrics")

        assert health_response.status_code == 200
        assert metrics_response.status_code == 200
        assert "trading_process_uptime_seconds" in metrics_response.text
        assert "trading_handler_duration_seconds" in metrics_response.text
