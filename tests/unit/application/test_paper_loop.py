from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from trading_platform.application.paper_loop import PaperTradingLoop
from trading_platform.backtesting.fill_simulator import FillSimulator
from trading_platform.backtesting.models.fee_model import FeeModel
from trading_platform.backtesting.models.latency_model import LatencyModel
from trading_platform.backtesting.models.partial_fill_model import PartialFillModel
from trading_platform.backtesting.models.spread_model import SpreadModel
from trading_platform.backtesting.order_queue import OrderQueue
from trading_platform.domain.events.market import BarClosed
from trading_platform.domain.events.system import Heartbeat
from trading_platform.domain.models.bar import Bar
from trading_platform.execution.paper_broker import PaperBroker
from trading_platform.infrastructure.event_bus.in_memory import InMemoryEventBus


class FakeFeed:
    def __init__(self, bars: list[Bar | None]) -> None:
        self._bars = list(bars)

    def poll_latest_closed_bar(self, symbol: str, timeframe: str) -> Bar | None:
        if not self._bars:
            return None
        return self._bars.pop(0)


class TestPaperTradingLoop:
    def test_processes_new_closed_bar_once(self, make_bar, btc_usdt_instrument_rules) -> None:
        bus = InMemoryEventBus()
        published: list[Any] = []
        original = bus.publish

        def recording_publish(event: Any) -> None:
            published.append(event)
            original(event)

        bus.publish = recording_publish  # type: ignore[method-assign]

        t0 = datetime(2024, 1, 1, tzinfo=UTC)
        bar = make_bar(timestamp=t0, close="100")
        stop_after = {"n": 0}

        def should_stop() -> bool:
            stop_after["n"] += 1
            return stop_after["n"] > 1

        broker = PaperBroker(
            FillSimulator(
                SpreadModel(5.0),
                FeeModel(assume_maker_on_limit=True),
                PartialFillModel(0.1),
                use_next_bar_open=True,
            ),
            OrderQueue(LatencyModel(1)),
            {"BTC/USDT": btc_usdt_instrument_rules},
        )
        loop = PaperTradingLoop(
            bus,
            FakeFeed([bar, None]),  # type: ignore[arg-type]
            broker,
            symbol="BTC/USDT",
            timeframe="1h",
            poll_interval_sec=0,
            should_stop=should_stop,
            sleep_fn=lambda _s: None,
        )
        n = loop.run()
        assert n == 1
        assert any(isinstance(e, BarClosed) for e in published)
        assert loop.last_bar_timestamp == t0

    def test_emits_heartbeat_when_idle(self, make_bar, btc_usdt_instrument_rules) -> None:
        bus = InMemoryEventBus()
        published: list[object] = []
        original = bus.publish

        def recording_publish(event: object) -> None:
            published.append(event)
            original(event)

        bus.publish = recording_publish  # type: ignore[method-assign]
        stop_after = {"n": 0}

        def should_stop() -> bool:
            stop_after["n"] += 1
            return stop_after["n"] > 1

        broker = PaperBroker(
            FillSimulator(
                SpreadModel(5.0),
                FeeModel(assume_maker_on_limit=True),
                PartialFillModel(0.1),
                use_next_bar_open=True,
            ),
            OrderQueue(LatencyModel(1)),
            {"BTC/USDT": btc_usdt_instrument_rules},
        )
        heartbeats: list[str] = []
        loop = PaperTradingLoop(
            bus,
            FakeFeed([None]),  # type: ignore[arg-type]
            broker,
            symbol="BTC/USDT",
            timeframe="1h",
            poll_interval_sec=30.0,
            last_bar_timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            should_stop=should_stop,
            sleep_fn=lambda _s: None,
            on_heartbeat=heartbeats.append,
        )
        n = loop.run()
        assert n == 0
        assert heartbeats
        assert "waiting for next closed candle" in heartbeats[0]
        assert "BTC/USDT@1h" in heartbeats[0]
        assert any(isinstance(e, Heartbeat) and e.mode == "paper" for e in published)
