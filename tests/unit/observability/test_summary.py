from __future__ import annotations

import logging

from trading_platform.observability.summary import (
    PeriodicSummaryLogger,
    SummaryTrackingMetricsCollector,
    _percentile,
)


class TestPercentile:
    def test_empty_samples_returns_zero(self) -> None:
        assert _percentile([], 99.0) == 0.0

    def test_single_sample(self) -> None:
        assert _percentile([5.0], 99.0) == 5.0

    def test_p50_of_uniform_samples(self) -> None:
        samples = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert _percentile(samples, 50.0) == 3.0

    def test_p99_is_near_the_max(self) -> None:
        samples = [float(i) for i in range(1, 101)]
        assert _percentile(samples, 99.0) >= 99.0


class TestSummaryTrackingMetricsCollector:
    def test_forwards_calls_to_inner_collector(self, fake_metrics) -> None:
        tracker = SummaryTrackingMetricsCollector(fake_metrics)

        tracker.increment_counter("trading_bars_processed_total", labels={"mode": "backtest"})
        tracker.observe_histogram(
            "trading_handler_duration_seconds", 0.05, labels={"handler": "strategy"}
        )
        tracker.set_gauge("trading_cpu_percent", 3.5)

        assert fake_metrics.counter_total("trading_bars_processed_total", mode="backtest") == 1
        assert fake_metrics.histograms[0].value == 0.05
        assert fake_metrics.gauges[0].value == 3.5

    def test_snapshot_and_reset_counts_clears_after_read(self, fake_metrics) -> None:
        tracker = SummaryTrackingMetricsCollector(fake_metrics)
        tracker.increment_counter("trading_bars_processed_total")
        tracker.increment_counter("trading_bars_processed_total")

        first_snapshot = tracker.snapshot_and_reset_counts()
        second_snapshot = tracker.snapshot_and_reset_counts()

        assert first_snapshot["trading_bars_processed_total"] == 2
        assert second_snapshot.get("trading_bars_processed_total", 0) == 0

    def test_snapshot_gauges_returns_latest_value(self, fake_metrics) -> None:
        tracker = SummaryTrackingMetricsCollector(fake_metrics)
        tracker.set_gauge("trading_cpu_percent", 1.0)
        tracker.set_gauge("trading_cpu_percent", 9.0)

        assert tracker.snapshot_gauges()["trading_cpu_percent"] == 9.0

    def test_latency_percentile_keyed_by_handler(self, fake_metrics) -> None:
        tracker = SummaryTrackingMetricsCollector(fake_metrics)
        for value in (0.001, 0.002, 0.003):
            tracker.observe_histogram(
                "trading_handler_duration_seconds", value, labels={"handler": "strategy"}
            )

        latencies = tracker.snapshot_latency_percentile_seconds(percentile=50.0)

        assert latencies["trading_handler_duration_seconds:strategy"] == 0.002


class TestPeriodicSummaryLogger:
    def test_maybe_emit_is_noop_before_interval_elapses(self, fake_metrics, caplog) -> None:
        tracker = SummaryTrackingMetricsCollector(fake_metrics)
        logger_ = PeriodicSummaryLogger(tracker, interval_seconds=3600.0)

        with caplog.at_level(logging.INFO):
            logger_.maybe_emit()

        assert not any(record.message == "metrics_summary" for record in caplog.records)

    def test_emit_logs_structured_summary_with_expected_keys(self, fake_metrics, caplog) -> None:
        tracker = SummaryTrackingMetricsCollector(fake_metrics)
        tracker.increment_counter("trading_bars_processed_total", value=10)
        tracker.observe_histogram(
            "trading_handler_duration_seconds", 0.002, labels={"handler": "strategy"}
        )
        tracker.set_gauge("trading_cpu_percent", 3.2)
        logger_ = PeriodicSummaryLogger(tracker, interval_seconds=60.0)

        with caplog.at_level(logging.INFO):
            logger_.emit(elapsed_seconds=10.0)

        record = next(r for r in caplog.records if r.message == "metrics_summary")
        assert record.bars_per_sec == 1.0
        assert record.strategy_latency_p99_ms == 2.0
        assert record.cpu_percent == 3.2
