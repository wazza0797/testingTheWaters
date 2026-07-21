from __future__ import annotations

from dataclasses import dataclass

from trading_platform.observability.system_monitor import (
    CPU_PERCENT_METRIC,
    MEMORY_RSS_METRIC,
    UPTIME_METRIC,
    SystemMonitor,
)


@dataclass
class _FakeMemInfo:
    rss: int


class _FakeProcess:
    def __init__(self, rss_bytes: int, cpu_percent: float, *, raise_on_cpu: bool = False) -> None:
        self._rss_bytes = rss_bytes
        self._cpu_percent = cpu_percent
        self._raise_on_cpu = raise_on_cpu

    def memory_info(self) -> _FakeMemInfo:
        return _FakeMemInfo(rss=self._rss_bytes)

    def cpu_percent(self, interval: float | None = None) -> float:
        if self._raise_on_cpu:
            raise OSError("psutil unavailable in this sandbox")
        return self._cpu_percent


class TestSystemMonitor:
    def test_poll_once_sets_memory_and_cpu_gauges(self, fake_metrics) -> None:
        process = _FakeProcess(rss_bytes=128 * 1024 * 1024, cpu_percent=4.2)
        monitor = SystemMonitor(fake_metrics, process=process)  # type: ignore[arg-type]

        monitor.poll_once()

        gauge_names = {call.name: call.value for call in fake_metrics.gauges}
        assert gauge_names[MEMORY_RSS_METRIC] == 128 * 1024 * 1024
        assert gauge_names[CPU_PERCENT_METRIC] == 4.2
        assert UPTIME_METRIC in gauge_names

    def test_uptime_increases_between_polls(self, fake_metrics) -> None:
        process = _FakeProcess(rss_bytes=1, cpu_percent=0.0)
        monitor = SystemMonitor(fake_metrics, process=process)  # type: ignore[arg-type]

        monitor.poll_once()
        first_uptime = next(c.value for c in fake_metrics.gauges if c.name == UPTIME_METRIC)
        monitor.poll_once()
        second_uptime = [c.value for c in fake_metrics.gauges if c.name == UPTIME_METRIC][-1]

        assert second_uptime >= first_uptime

    def test_cpu_poll_failure_does_not_raise_or_block_uptime(self, fake_metrics) -> None:
        process = _FakeProcess(rss_bytes=1, cpu_percent=0.0, raise_on_cpu=True)
        monitor = SystemMonitor(fake_metrics, process=process)  # type: ignore[arg-type]

        monitor.poll_once()  # must not raise

        gauge_names = {call.name for call in fake_metrics.gauges}
        assert UPTIME_METRIC in gauge_names
        assert CPU_PERCENT_METRIC not in gauge_names
