from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from trading_platform.analytics.handler import AnalyticsHandler
from trading_platform.analytics.state import RunningPerformanceState
from trading_platform.application.demo_loop import DemoTradingLoop
from trading_platform.application.paper_loop import PaperTradingLoop
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
from trading_platform.config.settings import Environment, Settings
from trading_platform.domain.errors import ConfigurationError
from trading_platform.domain.events.execution import FillReceived, OrderRejected
from trading_platform.domain.events.market import BarClosed
from trading_platform.domain.events.risk import OrderApproved, RiskRejected
from trading_platform.domain.events.strategy import SignalGenerated
from trading_platform.domain.events.system import ErrorOccurred, Heartbeat
from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.domain.ports.event_bus import IEventBus
from trading_platform.domain.ports.exchange import IExchangeAdapter
from trading_platform.domain.ports.market_data import IMarketDataRepository
from trading_platform.exchanges.factory import build_exchange_adapter
from trading_platform.execution.demo_broker import DemoBroker
from trading_platform.execution.handler import ExecutionHandler
from trading_platform.execution.paper_broker import PaperBroker
from trading_platform.infrastructure.event_bus.in_memory import InMemoryEventBus
from trading_platform.infrastructure.event_bus.timed import TimedEventBus
from trading_platform.infrastructure.metrics.prometheus import PrometheusMetricsCollector
from trading_platform.market_data.feed import PollingMarketDataFeed
from trading_platform.market_data.ingest import DataIngestService
from trading_platform.market_data.instrument_rules_cache import InstrumentRulesCache
from trading_platform.market_data.repository.parquet import ParquetMarketDataRepository
from trading_platform.notifications.factory import build_notifier
from trading_platform.notifications.handler import NotificationHandler
from trading_platform.observability.handler import MetricsHandler
from trading_platform.observability.server import HealthStatus, create_app
from trading_platform.observability.summary import (
    PeriodicSummaryLogger,
    SummaryTrackingMetricsCollector,
)
from trading_platform.observability.system_monitor import SystemMonitor
from trading_platform.portfolio.book import PortfolioBook
from trading_platform.portfolio.handler import PortfolioHandler
from trading_platform.portfolio.persistence import (
    JsonPaperStateStore,
    book_from_snapshot,
)
from trading_platform.portfolio.seed import seed_book_from_exchange
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
    notification_handler: NotificationHandler
    notification_executor: Executor

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

    notification_executor = ThreadPoolExecutor(
        max_workers=2,
        thread_name_prefix="notify",
    )
    notification_handler = NotificationHandler(
        build_notifier(settings),
        executor=notification_executor,
    )
    event_bus.subscribe(FillReceived, notification_handler)
    event_bus.subscribe(RiskRejected, notification_handler)
    event_bus.subscribe(OrderRejected, notification_handler)
    event_bus.subscribe(ErrorOccurred, notification_handler)
    event_bus.subscribe(Heartbeat, notification_handler)

    system_monitor = SystemMonitor(tracked_metrics)
    summary_logger = PeriodicSummaryLogger(
        tracked_metrics, interval_seconds=config.observability.log_summary_interval_sec
    )
    health = HealthStatus()

    data_dir = Path(settings.data_dir)
    # Public market-data adapter for ingest/paper/backtest/serve. Never force
    # DEMO credentials onto those commands when ENV=demo is set globally —
    # `build_demo_session` constructs its own sandbox adapter for orders.
    default_adapter_mode = (
        Environment.PAPER if settings.environment == Environment.DEMO else settings.environment
    )
    exchange_adapter = build_exchange_adapter(
        config.trading.exchange,
        default_adapter_mode,
        settings,
    )
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
        notification_handler=notification_handler,
        notification_executor=notification_executor,
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
    notification_handler: NotificationHandler | None = None

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
        # Same for notifications — avoid remote spam from simulated fills.
        if self.notification_handler is not None:
            self.event_bus.subscribe(FillReceived, self.notification_handler)
            self.event_bus.subscribe(OrderRejected, self.notification_handler)
            self.event_bus.subscribe(RiskRejected, self.notification_handler)


def build_backtest_engine(
    container: AppContainer,
    instrument_rules: InstrumentRules,
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
    strategy_params: Mapping[str, Any] | None = None,
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

    `strategy_params` is merged on top of `config.strategy.params` for this
    run only (used by walk-forward grid search so non-grid keys from YAML
    still apply). When omitted, config params apply unchanged.

    Safe to call more than once on the same container (e.g. hold-out IS then
    OOS) as long as each prior `BacktestRun` has been `teardown()`'d first.
    """
    config = container.config
    symbol = symbol or config.trading.symbol
    timeframe = timeframe or config.trading.timeframe
    backtest_config = config.backtest
    params = dict(config.strategy.params)
    if strategy_params is not None:
        params.update(strategy_params)

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

    strategy = instantiate_strategy(config.strategy.path, params)
    strategy_name = describe_strategy(config.strategy.path, symbol, params)
    strategy_context = DefaultStrategyContext(
        symbol=symbol,
        timeframe=timeframe,
        params=params,
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
    # Bypass NotificationHandler too — Discord/Telegram must not fire on
    # every simulated fill when paper remotes are configured in `.env`.
    container.event_bus.unsubscribe(FillReceived, container.notification_handler)
    container.event_bus.unsubscribe(OrderRejected, container.notification_handler)
    container.event_bus.unsubscribe(RiskRejected, container.notification_handler)

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
        notification_handler=container.notification_handler,
    )


@dataclass
class PaperSession:
    """Wired paper-trading session: loop + handlers + portfolio persistence."""

    loop: PaperTradingLoop
    portfolio_handler: PortfolioHandler
    strategy_handler: StrategyHandler
    risk_handler: RiskHandler
    execution_handler: ExecutionHandler
    event_bus: IEventBus
    symbol: str
    timeframe: str
    state_path: Path

    def teardown(self) -> None:
        self.strategy_handler.stop()
        self.event_bus.unsubscribe(BarClosed, self.strategy_handler)
        self.event_bus.unsubscribe(SignalGenerated, self.risk_handler)
        self.event_bus.unsubscribe(OrderApproved, self.execution_handler)
        self.event_bus.unsubscribe(FillReceived, self.portfolio_handler)
        self.event_bus.unsubscribe(BarClosed, self.portfolio_handler)


def build_paper_session(
    container: AppContainer,
    instrument_rules: InstrumentRules,
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
    should_stop: Callable[[], bool] | None = None,
    on_heartbeat: Callable[[str], None] | None = None,
) -> PaperSession:
    """Wire strategy → risk → execution → portfolio for `trading-platform paper`."""
    config = container.config
    symbol = symbol or config.trading.symbol
    timeframe = timeframe or config.trading.timeframe
    paper_cfg = config.paper
    backtest_config = config.backtest

    if config.strategy.path is None:
        raise ConfigurationError(
            "No strategy configured for paper trading — set 'strategy.path' in config/paper.yaml."
        )

    state_path = Path(container.settings.data_dir) / paper_cfg.state_file
    store = JsonPaperStateStore(state_path)
    snapshot = store.load()
    if snapshot is not None:
        book = book_from_snapshot(snapshot)
        last_bar_ts = snapshot.last_bar_timestamp
        container.analytics_state.fills.clear()
        container.analytics_state.fills.extend(snapshot.fills)
    else:
        book = PortfolioBook(starting_cash=paper_cfg.starting_cash)
        last_bar_ts = None
    container.analytics_state.starting_cash = paper_cfg.starting_cash

    portfolio_handler = PortfolioHandler(book, store, last_bar_timestamp=last_bar_ts)
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
    broker = PaperBroker(
        fill_simulator=fill_simulator,
        order_queue=OrderQueue(latency_model=LatencyModel(backtest_config.latency_bars)),
        instrument_rules=rules_by_symbol,
    )

    sizer = EquityFractionSizer(backtest_config.position_size_pct)
    risk_engine = PassThroughRiskEngine(
        portfolio_handler,
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

    params = dict(config.strategy.params)
    strategy = instantiate_strategy(config.strategy.path, params)
    strategy_name = describe_strategy(config.strategy.path, symbol, params)
    strategy_context = DefaultStrategyContext(
        symbol=symbol,
        timeframe=timeframe,
        params=params,
        position_provider=portfolio_handler,
    )
    strategy_handler = StrategyHandler(
        strategy, strategy_context, container.event_bus, symbol, timeframe, strategy_name
    )

    container.event_bus.subscribe(BarClosed, strategy_handler)
    container.event_bus.subscribe(SignalGenerated, risk_handler)
    container.event_bus.subscribe(OrderApproved, execution_handler)
    # Portfolio must see fills before NotificationHandler is (re)queued on the
    # same event — detach notify, attach portfolio, then re-attach notify last.
    container.event_bus.unsubscribe(FillReceived, container.notification_handler)
    container.event_bus.subscribe(FillReceived, portfolio_handler)
    container.event_bus.subscribe(BarClosed, portfolio_handler)
    container.event_bus.subscribe(FillReceived, container.notification_handler)

    feed = PollingMarketDataFeed(container.exchange_adapter)
    loop = PaperTradingLoop(
        container.event_bus,
        feed,
        broker,
        symbol=symbol,
        timeframe=timeframe,
        poll_interval_sec=paper_cfg.poll_interval_sec,
        last_bar_timestamp=last_bar_ts,
        should_stop=should_stop,
        on_heartbeat=on_heartbeat,
    )

    return PaperSession(
        loop=loop,
        portfolio_handler=portfolio_handler,
        strategy_handler=strategy_handler,
        risk_handler=risk_handler,
        execution_handler=execution_handler,
        event_bus=container.event_bus,
        symbol=symbol,
        timeframe=timeframe,
        state_path=state_path,
    )


@dataclass
class DemoSession:
    """Wired demo-trading session: exchange sandbox broker + portfolio from balances."""

    loop: DemoTradingLoop
    portfolio_handler: PortfolioHandler
    strategy_handler: StrategyHandler
    risk_handler: RiskHandler
    execution_handler: ExecutionHandler
    event_bus: IEventBus
    symbol: str
    timeframe: str
    state_path: Path

    def teardown(self) -> None:
        self.strategy_handler.stop()
        self.event_bus.unsubscribe(BarClosed, self.strategy_handler)
        self.event_bus.unsubscribe(SignalGenerated, self.risk_handler)
        self.event_bus.unsubscribe(OrderApproved, self.execution_handler)
        self.event_bus.unsubscribe(FillReceived, self.portfolio_handler)
        self.event_bus.unsubscribe(BarClosed, self.portfolio_handler)


def build_demo_session(
    container: AppContainer,
    instrument_rules: InstrumentRules,
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
    should_stop: Callable[[], bool] | None = None,
    on_heartbeat: Callable[[str], None] | None = None,
) -> DemoSession:
    """Wire strategy → risk → demo broker → portfolio for `trading-platform demo`.

    Requires `ENV=demo`. Cash/positions are seeded from the exchange adapter
    balances (not a local starting_cash). `trading.exchange` selects the adapter.
    """
    if container.settings.environment != Environment.DEMO:
        raise ConfigurationError(
            "trading-platform demo requires ENV=demo "
            "(and BINANCE_DEMO_API_KEY / BINANCE_DEMO_API_SECRET for Binance)."
        )

    config = container.config
    symbol = symbol or config.trading.symbol
    timeframe = timeframe or config.trading.timeframe
    demo_cfg = config.demo
    backtest_config = config.backtest

    if config.strategy.path is None:
        raise ConfigurationError(
            "No strategy configured for demo trading — set 'strategy.path' in config/demo.yaml."
        )

    # Re-bind adapter for this exchange+demo mode (container may have been built
    # before ENV was set correctly in tests).
    adapter = build_exchange_adapter(
        config.trading.exchange,
        Environment.DEMO,
        container.settings,
    )

    state_path = Path(container.settings.data_dir) / demo_cfg.state_file
    store = JsonPaperStateStore(state_path)
    snapshot = store.load()
    last_bar_ts = snapshot.last_bar_timestamp if snapshot is not None else None

    book = seed_book_from_exchange(adapter, symbol, timeframe=timeframe)
    if snapshot is not None:
        # Preserve fill history for analytics; cash/positions come from the venue.
        container.analytics_state.fills.clear()
        container.analytics_state.fills.extend(snapshot.fills)
        book = PortfolioBook.from_snapshot(
            book.cash,
            book.positions,
            timestamp=book.timestamp,
            fills=list(snapshot.fills),
        )
    container.analytics_state.starting_cash = book.cash

    portfolio_handler = PortfolioHandler(book, store, last_bar_timestamp=last_bar_ts)
    rules_by_symbol = {symbol: instrument_rules}
    broker = DemoBroker(adapter)

    sizer = EquityFractionSizer(backtest_config.position_size_pct)
    risk_engine = PassThroughRiskEngine(
        portfolio_handler,
        rules_by_symbol,
        sizer,
        broker,
        cash_safety_buffer_pct=backtest_config.cash_safety_buffer_pct,
        fill_cost_fraction=0.0,
    )
    risk_handler = RiskHandler(risk_engine, container.event_bus)
    execution_handler = ExecutionHandler(broker, rules_by_symbol, container.event_bus)

    params = dict(config.strategy.params)
    strategy = instantiate_strategy(config.strategy.path, params)
    strategy_name = describe_strategy(config.strategy.path, symbol, params)
    strategy_context = DefaultStrategyContext(
        symbol=symbol,
        timeframe=timeframe,
        params=params,
        position_provider=portfolio_handler,
    )
    strategy_handler = StrategyHandler(
        strategy, strategy_context, container.event_bus, symbol, timeframe, strategy_name
    )

    container.event_bus.subscribe(BarClosed, strategy_handler)
    container.event_bus.subscribe(SignalGenerated, risk_handler)
    container.event_bus.subscribe(OrderApproved, execution_handler)
    container.event_bus.unsubscribe(FillReceived, container.notification_handler)
    container.event_bus.subscribe(FillReceived, portfolio_handler)
    container.event_bus.subscribe(BarClosed, portfolio_handler)
    container.event_bus.subscribe(FillReceived, container.notification_handler)

    feed = PollingMarketDataFeed(adapter)
    loop = DemoTradingLoop(
        container.event_bus,
        feed,
        broker,
        symbol=symbol,
        timeframe=timeframe,
        poll_interval_sec=demo_cfg.order_poll_interval_sec,
        last_bar_timestamp=last_bar_ts,
        should_stop=should_stop,
        on_heartbeat=on_heartbeat,
    )

    return DemoSession(
        loop=loop,
        portfolio_handler=portfolio_handler,
        strategy_handler=strategy_handler,
        risk_handler=risk_handler,
        execution_handler=execution_handler,
        event_bus=container.event_bus,
        symbol=symbol,
        timeframe=timeframe,
        state_path=state_path,
    )
