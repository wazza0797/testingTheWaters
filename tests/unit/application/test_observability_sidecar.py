"""Observability sidecar used by paper/demo (Milestone 9)."""

from __future__ import annotations

from trading_platform.config.loader import load_config
from trading_platform.config.settings import Settings
from trading_platform.container import build_container
from trading_platform.main import _start_observability_sidecar


def test_observability_sidecar_noop_when_disabled() -> None:
    settings = Settings(_env_file=None, ENV="paper", OBSERVABILITY_ENABLED=False)
    container = build_container(settings, load_config(overlay="paper"))
    stop = _start_observability_sidecar(container)
    assert stop.is_set()
