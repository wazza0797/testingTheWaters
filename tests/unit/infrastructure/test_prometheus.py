from __future__ import annotations

from prometheus_client import CollectorRegistry

from trading_platform.infrastructure.metrics.prometheus import PrometheusMetricsCollector


class TestPrometheusMetricsCollector:
    def test_increment_counter_without_labels(self) -> None:
        collector = PrometheusMetricsCollector(registry=CollectorRegistry())
        collector.increment_counter("trading_test_total")
        collector.increment_counter("trading_test_total")

        output = collector.render_latest().decode()
        assert "trading_test_total 2.0" in output

    def test_increment_counter_with_labels(self) -> None:
        collector = PrometheusMetricsCollector(registry=CollectorRegistry())
        collector.increment_counter(
            "trading_bars_processed_total", labels={"mode": "backtest", "symbol": "BTC/USDT"}
        )

        output = collector.render_latest().decode()
        assert 'mode="backtest"' in output
        assert 'symbol="BTC/USDT"' in output

    def test_observe_histogram_produces_count_and_sum(self) -> None:
        collector = PrometheusMetricsCollector(registry=CollectorRegistry())
        collector.observe_histogram(
            "trading_handler_duration_seconds",
            0.01,
            labels={"handler": "strategy", "event_type": "BarClosed"},
        )

        output = collector.render_latest().decode()
        assert "trading_handler_duration_seconds_count" in output
        assert "trading_handler_duration_seconds_sum" in output

    def test_set_gauge_reflects_latest_value(self) -> None:
        collector = PrometheusMetricsCollector(registry=CollectorRegistry())
        collector.set_gauge("trading_cpu_percent", 1.0)
        collector.set_gauge("trading_cpu_percent", 5.5)

        output = collector.render_latest().decode()
        assert "trading_cpu_percent 5.5" in output

    def test_render_latest_returns_valid_prometheus_text_format(self) -> None:
        collector = PrometheusMetricsCollector(registry=CollectorRegistry())
        collector.increment_counter("trading_bars_processed_total", labels={"mode": "backtest"})
        collector.set_gauge("trading_process_uptime_seconds", 12.5)

        output = collector.render_latest().decode()
        assert output.startswith("# HELP") or "# HELP" in output
        assert "# TYPE trading_bars_processed_total counter" in output
        assert "# TYPE trading_process_uptime_seconds gauge" in output
