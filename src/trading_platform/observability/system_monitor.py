from __future__ import annotations

import logging
import time

import psutil

from trading_platform.domain.ports.metrics import IMetricsCollector

logger = logging.getLogger(__name__)

MEMORY_RSS_METRIC = "trading_memory_rss_bytes"
CPU_PERCENT_METRIC = "trading_cpu_percent"
UPTIME_METRIC = "trading_process_uptime_seconds"


class SystemMonitor:
    """Polls process-level CPU/memory via `psutil` and records them as gauges.

    `poll_once` is meant to be called on a timer (every `system_poll_interval_sec`,
    see `config/observability.yaml`) by the application's scheduler. It has no
    side effects beyond reading `/proc`-equivalent process stats and recording
    metrics, so it's safe to call directly in tests with a fake collector.

    Note: `psutil.Process.cpu_percent()` returns `0.0` on its first call in a
    process (it needs a prior sample to compute a delta) — this is expected.
    """

    def __init__(self, metrics: IMetricsCollector, process: psutil.Process | None = None) -> None:
        self._metrics = metrics
        self._process = process or psutil.Process()
        self._start_time = time.monotonic()

    def poll_once(self) -> None:
        """Poll and record gauges. Never raises: a monitoring poll must not be
        able to crash the process it's monitoring (e.g. restricted sandboxes/
        containers without full `/proc` access can make individual psutil
        calls fail — that's a reason to skip a sample, not to die).
        """
        uptime_seconds = time.monotonic() - self._start_time
        self._metrics.set_gauge(UPTIME_METRIC, uptime_seconds)

        try:
            memory_bytes = self._process.memory_info().rss
            self._metrics.set_gauge(MEMORY_RSS_METRIC, float(memory_bytes))
        except Exception:  # noqa: BLE001 — platform/psutil failures must not kill the poller
            logger.debug("system_monitor_memory_poll_failed", exc_info=True)
            memory_bytes = None

        try:
            cpu_percent = self._process.cpu_percent(interval=None)
            self._metrics.set_gauge(CPU_PERCENT_METRIC, cpu_percent)
        except Exception:  # noqa: BLE001 — platform/psutil failures must not kill the poller
            logger.debug("system_monitor_cpu_poll_failed", exc_info=True)
            cpu_percent = None

        logger.debug(
            "system_metrics_polled",
            extra={
                "memory_rss_mb": memory_bytes / (1024 * 1024) if memory_bytes else None,
                "cpu_percent": cpu_percent,
                "uptime_seconds": uptime_seconds,
            },
        )
