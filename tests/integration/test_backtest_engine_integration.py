from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from trading_platform.config.loader import load_config
from trading_platform.config.settings import Settings
from trading_platform.container import build_backtest_engine, build_container
from trading_platform.domain.events.execution import FillReceived, OrderRejected
from trading_platform.domain.events.market import BarClosed
from trading_platform.domain.events.risk import OrderApproved, RiskRejected
from trading_platform.domain.events.strategy import SignalGenerated
from trading_platform.domain.models.instrument_rules import InstrumentRules


def _synthetic_trending_bars(make_bar) -> list:
    """A plateau, a sharp rise, then a sharp fall — long enough (35 bars
    before the first move) for the real `config/backtest.yaml` strategy
    config (`fast_period=10`, `slow_period=30`) to see a golden cross on the
    rise and a death cross on the fall, exercising the full
    strategy -> risk -> execution -> fill pipeline end to end.
    """
    start = datetime(2024, 1, 1, tzinfo=UTC)
    closes = (
        ["100"] * 35  # flat plateau: both SMAs converge to 100
        + ["300"] * 40  # sharp rise: fast SMA(10) crosses above slow SMA(30)
        + ["50"] * 40  # sharp fall: fast SMA(10) crosses below slow SMA(30)
    )
    return [
        make_bar(
            timestamp=start + timedelta(hours=i),
            open_=c,
            high=c,
            low=c,
            close=c,
            volume="100",
        )
        for i, c in enumerate(closes)
    ]


class TestBacktestEngineIntegration:
    def test_full_pipeline_runs_a_real_strategy_through_simulated_fills(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        """Wires a real container from the actual `config/backtest.yaml`
        (the bundled `SmaCrossoverStrategy`, real risk sizing, real
        `SimBroker`) and drives it through a synthetic bar series designed
        to trigger both a BUY and a SELL — no network, no real exchange.
        """
        settings = Settings(_env_file=None)
        config = load_config(config_dir=Path("config"), overlay="backtest")
        container = build_container(settings, config)

        run = build_backtest_engine(container, btc_usdt_instrument_rules)
        bars = _synthetic_trending_bars(make_bar)

        result = run.engine.run(bars, timeframe="1h")
        run.strategy_handler.stop()

        assert result.bars_processed == len(bars)
        assert len(result.equity_curve) == len(bars)
        assert result.symbol == "BTC/USDT"
        assert result.starting_cash == config.backtest.starting_cash
        # The golden cross should have produced at least one real fill; the
        # subsequent death cross should have closed it back out.
        assert len(result.fills) >= 1

    def test_full_pipeline_publishes_the_expected_event_sequence_types(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        settings = Settings(_env_file=None)
        config = load_config(config_dir=Path("config"), overlay="backtest")
        container = build_container(settings, config)
        run = build_backtest_engine(container, btc_usdt_instrument_rules)
        bars = _synthetic_trending_bars(make_bar)

        recorded_events: list = []
        original_publish = container.event_bus.publish

        def _recording_publish(event: object) -> None:
            recorded_events.append(event)
            original_publish(event)

        container.event_bus.publish = _recording_publish  # type: ignore[method-assign]

        run.engine.run(bars, timeframe="1h")
        run.strategy_handler.stop()

        assert any(isinstance(e, BarClosed) for e in recorded_events)
        assert any(isinstance(e, SignalGenerated) for e in recorded_events)
        assert any(isinstance(e, OrderApproved) for e in recorded_events)
        assert any(isinstance(e, FillReceived) for e in recorded_events)
        # No rejections expected — the synthetic series is sized comfortably
        # within min_qty/min_notional for the configured starting cash.
        assert not any(isinstance(e, RiskRejected) for e in recorded_events)
        assert not any(isinstance(e, OrderRejected) for e in recorded_events)
