from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_platform.domain.models.bar import Bar
from trading_platform.domain.models.instrument_rules import InstrumentRules


@dataclass
class RecordedCall:
    name: str
    value: float
    labels: dict[str, str]


class FakeMetricsCollector:
    """In-memory `IMetricsCollector` for unit tests — no `prometheus_client`
    side effects (no global registry, no metric-name collisions across tests).
    """

    def __init__(self) -> None:
        self.counters: list[RecordedCall] = []
        self.histograms: list[RecordedCall] = []
        self.gauges: list[RecordedCall] = []

    def increment_counter(
        self, name: str, labels: dict[str, str] | None = None, value: float = 1.0
    ) -> None:
        self.counters.append(RecordedCall(name, value, labels or {}))

    def observe_histogram(
        self, name: str, value: float, labels: dict[str, str] | None = None
    ) -> None:
        self.histograms.append(RecordedCall(name, value, labels or {}))

    def set_gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        self.gauges.append(RecordedCall(name, value, labels or {}))

    def counter_total(self, name: str, **labels: str) -> float:
        return sum(
            call.value
            for call in self.counters
            if call.name == name and all(call.labels.get(k) == v for k, v in labels.items())
        )


@pytest.fixture
def fake_metrics() -> FakeMetricsCollector:
    return FakeMetricsCollector()


@pytest.fixture
def make_bar() -> Callable[..., Bar]:
    def _make_bar(
        symbol: str = "BTC/USDT",
        timeframe: str = "1h",
        timestamp: datetime | None = None,
        open_: str = "100",
        high: str = "110",
        low: str = "90",
        close: str = "105",
        volume: str = "10",
    ) -> Bar:
        return Bar(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=timestamp or datetime(2024, 1, 1, tzinfo=UTC),
            open=Decimal(open_),
            high=Decimal(high),
            low=Decimal(low),
            close=Decimal(close),
            volume=Decimal(volume),
        )

    return _make_bar


@pytest.fixture
def btc_usdt_instrument_rules() -> InstrumentRules:
    return InstrumentRules(
        exchange="binance",
        symbol="BTC/USDT",
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.00001"),
        min_qty=Decimal("0.00001"),
        min_notional=Decimal("10"),
        price_precision=2,
        qty_precision=5,
        maker_fee_rate=Decimal("0.001"),
        taker_fee_rate=Decimal("0.001"),
    )
