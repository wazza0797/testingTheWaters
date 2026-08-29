from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI

from trading_platform.analytics.handler import AnalyticsHandler
from trading_platform.analytics.state import RunningPerformanceState
from trading_platform.backtesting.broker_sim import SimBroker
from trading_platform.backtesting.engine import BacktestEngine
from trading_platform.backtesting.fill_simulator import FillSimulator
from trading_platform.backtesting.ledger import BacktestLedger
from trading_platform.backtesting.models.fee_model import FeeModel
from trading_platform.backtesting.models.latency_model import LatencyModel
from trading_platform.backtesting.models.partial_fill_model import PartialFillModel
from trading_platform.backtesting.models.spread_model import SpreadModel, max_half_spread_fraction
from trading_platform.backtesting.order_queue import OrderQueue
from trading_platform.config.loader import AppConfig
from trading_platform.config.settings import Settings
from trading_platform.domain.errors import ConfigurationError
from trading_platform.domain.events.execution import FillReceived, OrderRejected
from trading_platform.domain.events.market import BarClosed
from trading_platform.domain.events.risk import OrderApproved, RiskRejected
from trading_platform.domain.events.strategy import SignalGenerated
from trading_platform.domain.events.system import Heartbeat
from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.domain.ports.event_bus import IEventBus
from trading_platform.domain.ports.exchange import IExchangeAdapter
from trading_platform.domain.ports.market_data import IMarketDataRepository
from trading_platform.exchanges.binance.adapter import BinanceAdapter
from trading_platform.execution.handler import ExecutionHandler
from trading_platform.infrastructure.event_bus.in_memory import InMemoryEventBus
from trading_platform.infrastructure.event_bus.timed import TimedEventBus
from trading_platform.infrastructure.metrics.prometheus import PrometheusMetricsCollector
from trading_platform.market_data.ingest import DataIngestService
from trading_platform.market_data.instrument_rules_cache import InstrumentRulesCache
from trading_platform.market_data.repository.parquet import ParquetMarketDataRepository
from trading_platform.observability.handler import MetricsHandler
from trading_platform.observability.server import HealthStatus, create_app
from trading_platform.observability.summary import (
    PeriodicSummaryLogger,
    SummaryTrackingMetricsCollector,
)
from trading_platform.observability.system_monitor import SystemMonitor
from trading_platform.risk.engine import PassThroughRiskEngine
from trading_platform.risk.handler import RiskHandler
from trading_platform.risk.sizing import EquityFractionSizer
from trading_platform.strategies.context import DefaultStrategyContext
from trading_platform.strategies.handler import StrategyHandler
from trading_platform.strategies.loader import describe_strategy, instantiate_strategy

# Every event type MetricsHandler translates into a throughput counter (see
# docs/architecture.md metric catalog). Strategy/risk/execution/notification
# handlers are added to this wiring in their respective milestones (M3/M4/M6/M7)
# — nothing here should need to change when they land.
#
# Heartbeat is included even though MetricsHandler doesn't count it: it gives
# the M0 skeleton (which has no strategy/risk/execution handlers yet) at least
# one real handler invocation to exercise the TimedEventBus latency pipeline
# end-to-end (see `main.py`'s background loop and the M0 acceptance criteria).
_METRICS_HANDLER_EVENT_TYPES = (
    BarClosed,
    SignalGenerated,
    OrderApproved,
    RiskRejected,
    OrderRejected,
    FillReceived,
    Heartbeat,
)


@dataclass
class AppContainer:
    """Composition root: owns every long-lived singleton and wires event bus
    subscriptions. Nothing outside this module should construct handlers or
    the event bus directly (see coding standards: "register subscriptions
    only in container.py").
    """

    settings: Settings
    config: AppConfig
    event_bus: IEventBus
    prometheus_collector: PrometheusMetricsCollector
    system_monitor: SystemMonitor
    summary_logger: PeriodicSummaryLogger
    health: HealthStatus
    exchange_adapter: IExchangeAdapter
    market_data_repository: IMarketDataRepository
    instrument_rules_cache: InstrumentRulesCache
    data_ingest_service: DataIngestService
    analytics_state: RunningPerformanceState
    analytics_handler: AnalyticsHandler

    def observability_app(self) -> FastAPI:
        return create_app(self.prometheus_collector, self.health)


def build_container(settings: Settings, config: AppConfig) -> AppContainer:
    prometheus_collector = PrometheusMetricsCollector()
    tracked_metrics = SummaryTrackingMetricsCollector(prometheus_collector)

    inner_bus = InMemoryEventBus()
    event_bus = TimedEventBus(inner_bus, tracked_metrics)

    metrics_handler = MetricsHandler(tracked_metrics)
    for event_type in _METRICS_HANDLER_EVENT_TYPES:
        event_bus.subscribe(event_type, metrics_handler)

    analytics_state = RunningPerformanceState(
        starting_cash=config.backtest.starting_cash,
    )
    analytics_handler = AnalyticsHandler(analytics_state)
    event_bus.subscribe(FillReceived, analytics_handler)
    event_bus.subscribe(OrderRejected, analytics_handler)
    event_bus.subscribe(RiskRejected, analytics_handler)

    system_monitor = SystemMonitor(tracked_metrics)
    summary_logger = PeriodicSummaryLogger(
        tracked_metrics, interval_seconds=config.observability.log_summary_interval_sec
    )
    health = HealthStatus()

    data_dir = Path(settings.data_dir)
    exchange_adapter = BinanceAdapter()
    market_data_repository = ParquetMarketDataRepository(data_dir, exchange=config.trading.exchange)
    instrument_rules_cache = InstrumentRulesCache(data_dir)
    data_ingest_service = DataIngestService(exchange_adapter, market_data_repository, event_bus)

    return AppContainer(
        settings=settings,
        config=config,
        event_bus=event_bus,
        prometheus_collector=prometheus_collector,
        system_monitor=system_monitor,
        summary_logger=summary_logger,
        health=health,
        exchange_adapter=exchange_adapter,
        market_data_repository=market_data_repository,
        instrument_rules_cache=instrument_rules_cache,
        data_ingest_service=data_ingest_service,
        analytics_state=analytics_state,
        analytics_handler=analytics_handler,
    )


@dataclass
class BacktestRun:
    """Everything the `backtest` CLI command needs to execute one run: a
    fully-wired `BacktestEngine` (strategy -> risk -> execution already
    subscribed on `container.event_bus`) plus the resolved symbol/timeframe
    it was built for.

    Call `teardown()` when the run is finished (or between hold-out windows)
    so subscriptions don't leak into a subsequent run on the same bus.
    """

    engine: BacktestEngine
    strategy_handler: StrategyHandler
    risk_handler: RiskHandler
    execution_handler: ExecutionHandler
    event_bus: IEventBus
    symbol: str
    timeframe: str
    analytics_handler: AnalyticsHandler | None = None

    def teardown(self) -> None:
        """Stop the strategy and unsubscribe this run's handlers from the bus."""
        self.strategy_handler.stop()
        self.event_bus.unsubscribe(BarClosed, self.strategy_handler)
        self.event_bus.unsubscribe(SignalGenerated, self.risk_handler)
        self.event_bus.unsubscribe(OrderApproved, self.execution_handler)
        # Re-attach analytics after a backtest window — M5 reports from
        # `BacktestResult` post-run; the live handler must not accumulate
        # simulated fills (see Milestone 5 design §6).
        if self.analytics_handler is not None:
            self.event_bus.subscribe(FillReceived, self.analytics_handler)
            self.event_bus.subscribe(OrderRejected, self.analytics_handler)
            self.event_bus.subscribe(RiskRejected, self.analytics_handler)


def build_backtest_engine(
    container: AppContainer,
    instrument_rules: InstrumentRules,
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
) -> BacktestRun:
    """Wire up one backtest run's strategy -> risk -> execution chain onto
    `container.event_bus`, reusing the container's existing singletons
    (event bus, config).

    Split out from `build_container` because this wiring needs
    `instrument_rules` — a cache/exchange round trip the caller
    (`main.py`'s `backtest` command) performs, that `serve`/`download-data`
    have no reason to pay for at every startup.

    `symbol` / `timeframe` default to `config.trading.*` but may be overridden
    by CLI flags on `trading-platform backtest` without editing YAML.

    Safe to call more than once on the same container (e.g. hold-out IS then
    OOS) as long as each prior `BacktestRun` has been `teardown()`'d first.
    """
    config = container.config
    symbol = symbol or config.trading.symbol
    timeframe = timeframe or config.trading.timeframe
    backtest_config = config.backtest

    if config.strategy.path is None:
        raise ConfigurationError(
            "No strategy configured for backtesting — set 'strategy.path' "
            "(and optionally 'strategy.params') in config/backtest.yaml. See "
            "strategies/loader.py::load_strategy_class for the "
            "'module:ClassName' path format."
        )

    ledger = BacktestLedger(starting_cash=backtest_config.starting_cash)
    rules_by_symbol = {symbol: instrument_rules}

    fill_simulator = FillSimulator(
        spread_model=SpreadModel(
            backtest_config.spread_bps,
            volatility_k=backtest_config.spread_volatility_k,
            atr_period=backtest_config.spread_atr_period,
        ),
        fee_model=FeeModel(assume_maker_on_limit=backtest_config.assume_maker_on_limit),
        partial_fill_model=PartialFillModel(backtest_config.volume_participation_rate),
        use_next_bar_open=backtest_config.use_next_bar_open,
    )
    broker = SimBroker(
        fill_simulator=fill_simulator,
        order_queue=OrderQueue(latency_model=LatencyModel(backtest_config.latency_bars)),
        instrument_rules=rules_by_symbol,
    )

    sizer = EquityFractionSizer(backtest_config.position_size_pct)
    risk_engine = PassThroughRiskEngine(
        ledger,
        rules_by_symbol,
        sizer,
        broker,
        cash_safety_buffer_pct=backtest_config.cash_safety_buffer_pct,
        fill_cost_fraction=float(
            max_half_spread_fraction(
                backtest_config.spread_bps, backtest_config.spread_volatility_k
            )
        ),
    )
    risk_handler = RiskHandler(risk_engine, container.event_bus)

    execution_handler = ExecutionHandler(broker, rules_by_symbol, container.event_bus)

    strategy = instantiate_strategy(config.strategy.path, config.strategy.params)
    strategy_name = describe_strategy(config.strategy.path, symbol, config.strategy.params)
    strategy_context = DefaultStrategyContext(
        symbol=symbol,
        timeframe=timeframe,
        params=config.strategy.params,
        position_provider=ledger,
    )
    strategy_handler = StrategyHandler(
        strategy, strategy_context, container.event_bus, symbol, timeframe, strategy_name
    )

    container.event_bus.subscribe(BarClosed, strategy_handler)
    container.event_bus.subscribe(SignalGenerated, risk_handler)
    container.event_bus.subscribe(OrderApproved, execution_handler)

    # Bypass AnalyticsHandler during simulated fills — post-run
    # `PerformanceReport` over `BacktestResult` is the source of truth.
    container.event_bus.unsubscribe(FillReceived, container.analytics_handler)
    container.event_bus.unsubscribe(OrderRejected, container.analytics_handler)
    container.event_bus.unsubscribe(RiskRejected, container.analytics_handler)

    engine = BacktestEngine(container.event_bus, broker, ledger, symbol)

    return BacktestRun(
        engine=engine,
        strategy_handler=strategy_handler,
        risk_handler=risk_handler,
        execution_handler=execution_handler,
        event_bus=container.event_bus,
        symbol=symbol,
        timeframe=timeframe,
        analytics_handler=container.analytics_handler,
    )
