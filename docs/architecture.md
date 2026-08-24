# Architecture

## Executive Summary

A modular, extensible trading platform — not a monolithic bot. Strategies emit
signals; a risk engine approves or rejects them; execution fills orders.
Every module communicates through a typed, in-process **event bus** — no
module calls another directly. Operational metrics (throughput, handler
latency, CPU/memory) are collected from Milestone 0 onward, in every mode
(backtest, paper, live).

The first vertical slice targets **BTC/USDT on Binance** via a pluggable
exchange adapter, with **Parquet** historical data, **realistic backtesting**
(spread, partial fills, order latency, maker/taker fees, exchange precision
rules), paper trading, and Docker/VPS deployment.

## Layer Model (Clean Architecture + Event Bus)

```mermaid
flowchart TB
    subgraph interfaces [Interfaces]
        CLI[CLI]
        Scheduler[Scheduler]
        HealthHTTP[HealthEndpoint]
    end

    subgraph application [Application Layer]
        BacktestRunner[BacktestRunner]
        PaperTrader[PaperTrader]
        LiveTrader[LiveTrader]
        DataIngest[DataIngestService]
        TradingLoop[TradingLoop]
    end

    subgraph eventBus [Event Bus]
        Bus[IEventBus]
        BarClosed[BarClosed]
        SignalGenerated[SignalGenerated]
        OrderApproved[OrderApproved]
        FillReceived[FillReceived]
        RiskRejected[RiskRejected]
    end

    subgraph handlers [Event Handlers]
        StrategyHandler[StrategyHandler]
        RiskHandler[RiskHandler]
        ExecutionHandler[ExecutionHandler]
        PortfolioHandler[PortfolioHandler]
        NotificationHandler[NotificationHandler]
        AnalyticsHandler[AnalyticsHandler]
        MetricsHandler[MetricsHandler]
    end

    subgraph observability [Observability]
        MetricsCollector[IMetricsCollector]
        MetricsHTTP[MetricsEndpoint]
        SystemMonitor[SystemMonitor]
    end

    subgraph domain [Domain Layer]
        IStrategy[IStrategy]
        IExchange[IExchangeAdapter]
        IMarketDataRepo[IMarketDataRepository]
    end

    subgraph infrastructure [Infrastructure]
        InMemoryBus[InMemoryEventBus]
        BinanceAdapter[BinanceAdapter]
        ParquetRepo[ParquetMarketDataRepository]
        PaperBroker[PaperBroker]
        LiveBroker[LiveBroker]
    end

    CLI --> BacktestRunner
    CLI --> PaperTrader
    Scheduler --> PaperTrader

    BacktestRunner --> TradingLoop
    PaperTrader --> TradingLoop
    TradingLoop --> Bus
    MetricsHandler --> MetricsCollector
    SystemMonitor --> MetricsCollector
    MetricsHTTP --> MetricsCollector
    HealthHTTP --> MetricsCollector

    Bus --> MetricsHandler
    Bus --> StrategyHandler
    Bus --> RiskHandler
    Bus --> ExecutionHandler
    Bus --> PortfolioHandler
    Bus --> NotificationHandler
    Bus --> AnalyticsHandler

    StrategyHandler --> IStrategy
    ExecutionHandler --> PaperBroker
    ExecutionHandler --> LiveBroker
    DataIngest --> IExchange

    InMemoryBus -.-> Bus
    BinanceAdapter -.-> IExchange
    ParquetRepo -.-> IMarketDataRepo
```

Milestone 0 implements everything in `observability`, the event bus
(`InMemoryEventBus` + `TimedEventBus`), `MetricsHandler`, domain models/events/
ports, config, and CLI skeleton. `StrategyHandler`, `RiskHandler`,
`ExecutionHandler`, `PortfolioHandler`, `NotificationHandler`,
`AnalyticsHandler`, and the exchange/broker adapters land in later milestones
(see the project roadmap) — the wiring seams for all of them already exist in
`domain/ports/` and `container.py`.

## Event Flow (Paper / Live / Backtest)

All runtime modes share the same event pipeline. The `TradingLoop` (or
`BacktestEngine` replay) is the only component that drives time forward — it
publishes `BarClosed` events. Everything else reacts via subscriptions wired
in the composition root (`container.py`).

```mermaid
sequenceDiagram
    participant Loop as TradingLoop
    participant Bus as EventBus
    participant ST as StrategyHandler
    participant RK as RiskHandler
    participant EX as ExecutionHandler
    participant PF as PortfolioHandler
    participant NT as NotificationHandler
    participant AN as AnalyticsHandler

    Loop->>Bus: publish BarClosed
    Bus->>ST: on_bar_closed
    ST->>Bus: publish SignalGenerated
    Bus->>RK: on_signal
    alt Approved
        RK->>Bus: publish OrderApproved
        Bus->>EX: on_order_approved
        EX->>Bus: publish FillReceived
        Bus->>PF: on_fill
        Bus->>NT: on_fill
        Bus->>AN: on_fill
    else Rejected
        RK->>Bus: publish RiskRejected
        Bus->>NT: on_risk_rejected
    end
```

**Handler chain order** is fixed at startup (strategy → risk → execution).
Side-effect handlers (portfolio, notifications, analytics) subscribe to
downstream events and must never block the critical path.

## Dependency Rules

| Layer | May depend on | Must NOT depend on |
|-------|---------------|-------------------|
| Domain | Nothing external | Infrastructure, CLI, ccxt, pandas |
| Application | Domain interfaces + `IEventBus` | Concrete adapters, handler implementations |
| Infrastructure | Domain + third-party libs | Strategy logic |
| Interfaces | Application + DI container | Direct ccxt calls; direct handler-to-handler calls |

**Event bus rule:** Modules publish and subscribe to **typed domain events**
only. Handlers must not import peer handlers. All subscriptions are wired in
[`container.py`](../src/trading_platform/container.py).

## Key Design Decisions

### 1. Exchange Adapter Pattern (Binance is an implementation detail)

[`IExchangeAdapter`](../src/trading_platform/domain/ports/exchange.py) defines
`fetch_ohlcv`, `fetch_instrument_rules`, `place_order`, `cancel_order`, and
`get_balance`. All ccxt/Binance code will live in `exchanges/binance/`
(Milestone 1). Application code depends only on the port.

### 2. Market Data Repository (Parquet canonical)

[`IMarketDataRepository`](../src/trading_platform/domain/ports/market_data.py)
abstracts historical OHLCV storage. Parquet layout (Milestone 1):
`data/ohlcv/{exchange}/{symbol}/{timeframe}/YYYY-MM.parquet`. Future backends
(SQLite, S3, TimescaleDB) implement the same port.

### 3. Strategy Plugin Contract

Strategies implement
[`IStrategy`](../src/trading_platform/domain/ports/strategy.py):

```python
class IStrategy(Protocol):
    def on_start(self, ctx: StrategyContext) -> None: ...
    def on_bar(self, bar: Bar, ctx: StrategyContext) -> list[Signal]: ...
    def on_stop(self, ctx: StrategyContext) -> None: ...
```

`StrategyContext` (Milestone 3) is itself a `Protocol` — not a concrete
class — so `domain/ports/strategy.py` never needs to import `pandas` or
`indicators/`; it exposes:

- `symbol`, `timeframe`, `params` (strategy-specific config from `config/*.yaml`)
- `indicator(name, bars, **kwargs) -> float`: latest value of a named
  indicator (`indicators.IndicatorRegistry`) computed over a bar sequence the
  strategy accumulates itself (`on_bar` only ever receives one new `Bar` at a
  time — no history is pushed in). Returns `NaN` on insufficient history.
- `position_for(symbol) -> Position | None`: read-only positions, backed by
  [`IPositionProvider`](../src/trading_platform/domain/ports/portfolio.py).
  Milestone 3 wires the stub `NullPositionProvider` (always flat — no
  position tracking exists yet); Milestone 5's `PortfolioHandler` supplies
  the real implementation later with no strategy-facing change.

[`StrategyHandler`](../src/trading_platform/strategies/handler.py) adapts one
strategy instance to the event bus: subscribes to `BarClosed` filtered to its
own symbol/timeframe, calls `on_start` once lazily, and publishes a
`SignalGenerated` (reusing the triggering bar's `correlation_id`) per
returned `Signal`. Running several strategies (or one strategy across several
symbols) means constructing several `StrategyHandler`s — nothing in the class
itself changes. **Not yet wired into `container.py`**: there is no
`TradingLoop`/`BacktestEngine` to drive `BarClosed` for a real trading mode
until Milestone 4, and wiring it prematurely would react to the
`BarClosed(mode="ingest")` events `download-data` already publishes.

[`StrategyLoader`](../src/trading_platform/strategies/loader.py) resolves a
strategy purely from a `"module:ClassName"` config string
(`config.strategy.path` / `config.strategy.params`) via `importlib` — adding
a strategy is a new file + that one config string, with zero changes to the
loader or any other core module (empirically verified, not just asserted —
see the M3 milestone doc).

Strategies must have **zero imports** from `exchanges/`, `execution/`, or
`ccxt`, and must be fully testable with synthetic `Bar` sequences — see the
reference [`SmaCrossoverStrategy`](../src/trading_platform/strategies/examples/sma_crossover.py).

### 4. Internal Event Bus (In-Process Pub/Sub)

The event bus is the **primary integration mechanism** between modules,
avoiding direct imports between strategy, risk, execution, portfolio,
analytics, and notifications.

- **Port:** [`IEventBus`](../src/trading_platform/domain/ports/event_bus.py)
- **Implementation:** [`InMemoryEventBus`](../src/trading_platform/infrastructure/event_bus/in_memory.py) —
  synchronous, single-threaded, deterministic. Handlers for a given event type
  run in registration order.
- **Domain events** ([`domain/events/`](../src/trading_platform/domain/events/)):
  frozen dataclasses with `kw_only=True` fields, carrying a `correlation_id`
  and `timestamp` (see [`base.py`](../src/trading_platform/domain/events/base.py)).

| Event | Published by | Consumed by |
|-------|-------------|-------------|
| `BarClosed` | TradingLoop / BacktestEngine | StrategyHandler |
| `SignalGenerated` | StrategyHandler | RiskHandler |
| `OrderApproved` | RiskHandler | ExecutionHandler |
| `RiskRejected` | RiskHandler | NotificationHandler |
| `OrderRejected` | ExecutionHandler / SimBroker | NotificationHandler, AnalyticsHandler |
| `FillReceived` | ExecutionHandler / SimBroker | PortfolioHandler, NotificationHandler, AnalyticsHandler |
| `ErrorOccurred` | Any handler | NotificationHandler, logging |
| `Heartbeat` | TradingLoop | NotificationHandler, health endpoint |

**Design constraints:**
- Events carry **immutable snapshots** (bar, signal, fill) — not live object references.
- Every event includes `correlation_id` and `timestamp` for tracing.
- Side-effect handlers (notifications, analytics) must catch their own
  exceptions — a failure there must never block a fill.
- Critical handlers (strategy, risk, execution) propagate exceptions so the
  loop can halt and publish `ErrorOccurred`.
- Adding a feature = new handler + subscription in `container.py` — no
  changes to existing modules.

**Future extension path:** swap `InMemoryEventBus` for a Redis/RabbitMQ
adapter implementing the same `IEventBus` port when running multi-process.
Event types remain unchanged.

### 5. Risk Sits Between Strategy and Execution (via Events)

Initial implementation (Milestone 6): pass-through risk engine (approves all
signals) with hook points for rules. `RiskHandler` subscribes to
`SignalGenerated` and publishes `OrderApproved` or `RiskRejected`. No direct
call from strategy to execution.

### 6. Backtest and Paper Share the Same Event Pipeline

Both modes use `TradingLoop` + `EventBus` + the same handler chain. Backtest
replays historical bars; paper polls live bars. Only the bar source and broker
implementation differ. Both `SimBroker` and `PaperBroker` delegate to a shared
`FillSimulator` pipeline (Milestone 4) so fill realism stays consistent
across modes.

### 7. Realistic Backtest Fill Simulation (Milestone 4)

Backtesting must model exchange microstructure constraints, not just
bar-close fills with a flat fee. A `FillSimulator` will apply rules in a fixed
pipeline:

```mermaid
flowchart LR
    OrderApproved --> Validator
    Validator --> LatencyQueue
    LatencyQueue --> SpreadModel
    SpreadModel --> PartialFill
    PartialFill --> FeeModel
    FeeModel --> FillReceived
```

`InstrumentRules` (`tick_size`, `step_size`, `min_qty`, `min_notional`,
`price_precision`, `qty_precision`, `maker_fee_rate`, `taker_fee_rate`) are
fetched from the exchange adapter and cached to
`data/instruments/{exchange}/{symbol}.json`. Rounding/validation will live in
`execution/precision.py`, shared by `SimBroker`, `PaperBroker`, and
`LiveBroker`.

Configured via `config/backtest.yaml` (`spread_bps`, `latency_bars`,
`volume_participation_rate`, `assume_maker_on_limit`, `use_next_bar_open`).

**Limitations (by design, documented rather than hidden):**
- OHLCV-only data cannot reproduce true L2 order book dynamics — spread and
  partial fills are *approximations*.
- Intrabar price path is unknown — a limit fill is assumed if the bar's
  high/low range crosses the limit price.
- Future upgrade path: tick/trade data feed for higher-fidelity simulation
  without changing the `FillSimulator` interface.

### 8. Configuration Split

| Source | Contents |
|--------|----------|
| `config/*.yaml` | Symbols, timeframes, strategy params, backtest/observability tuning |
| Environment variables | API keys, Telegram token, `LOG_LEVEL`, `ENV=paper\|live` |
| `.env` (local only, gitignored) | Convenience for dev; never committed |

[`Settings`](../src/trading_platform/config/settings.py) (Pydantic
`BaseSettings`) owns every env-backed field. [`config/loader.py`](../src/trading_platform/config/loader.py)
loads and deep-merges YAML into a validated `AppConfig`. Never mix the two —
secrets never go in YAML, and strategy/backtest params never go in env vars.

### 9. Operational Observability (Metrics from Day One)

**Distinction:** [`observability/`](../src/trading_platform/observability/)
collects **runtime/system metrics** (throughput, latency, CPU). `analytics/`
(Milestone 5) computes **trading performance** (Sharpe, drawdown, P&L). These
must not be conflated.

```mermaid
flowchart LR
    TimedBus[TimedEventBus] --> InMemoryBus[InMemoryEventBus]
    TimedBus --> Histograms[handler_duration_seconds]
    MetricsHandler --> Counters[bars_signals_orders_total]
    SystemMonitor --> Gauges[cpu_memory_gauges]
    PrometheusCollector --> MetricsHTTP["GET /metrics"]
    SummaryLogger --> Stdout[Periodic log summary]
```

**Port:** [`IMetricsCollector`](../src/trading_platform/domain/ports/metrics.py) —
a thin abstraction over `prometheus_client` for testability (see
`tests/conftest.py::FakeMetricsCollector`).

#### Metric Catalog

| Metric | Type | Labels | Source |
|--------|------|--------|--------|
| `trading_bars_processed_total` | Counter | `mode`, `symbol` | `MetricsHandler` on `BarClosed` |
| `trading_signals_generated_total` | Counter | `strategy`, `symbol` | `MetricsHandler` on `SignalGenerated` |
| `trading_orders_submitted_total` | Counter | `symbol`, `side` | `MetricsHandler` on `OrderApproved` |
| `trading_orders_rejected_total` | Counter | `reason` | `MetricsHandler` on `OrderRejected`, `RiskRejected` |
| `trading_fills_received_total` | Counter | `symbol`, `fee_type` | `MetricsHandler` on `FillReceived` |
| `trading_events_published_total` | Counter | `event_type` | `TimedEventBus` |
| `trading_handler_duration_seconds` | Histogram | `handler`, `event_type` | `TimedEventBus` per handler invocation |
| `trading_handler_errors_total` | Counter | `handler`, `error_type` | `TimedEventBus` on handler exception |
| `trading_memory_rss_bytes` | Gauge | — | `SystemMonitor` (psutil) |
| `trading_cpu_percent` | Gauge | — | `SystemMonitor` (psutil) |
| `trading_process_uptime_seconds` | Gauge | — | `SystemMonitor` |

Each metric **name** must always be called with the same fixed label-key set —
`PrometheusMetricsCollector` lazily creates one Prometheus object per (name,
label-keys) pair and Prometheus does not support redefining a metric's labels.

**Derived rates** (bars/sec, signals/sec, orders/sec) and **latency
percentiles** (p99 per handler) are computed in-process by
[`SummaryTrackingMetricsCollector`](../src/trading_platform/observability/summary.py)
and logged periodically (default every 60s, see
`config/observability.yaml::log_summary_interval_sec`) in the shape:

```json
{"bars_per_sec": 42.5, "signals_per_sec": 0.1, "orders_per_sec": 0.05,
 "strategy_latency_p99_ms": 1.2, "risk_latency_p99_ms": 0.3, "execution_latency_p99_ms": 0.8,
 "memory_rss_mb": 128, "cpu_percent": 4.2}
```

The same rates can also be computed via PromQL `rate()` once a real
Prometheus server scrapes `/metrics` (Milestone 9).

#### Exposure

- **M0:** `GET /metrics` (Prometheus text format), `GET /health` (process up +
  uptime). Both served by [`observability/server.py`](../src/trading_platform/observability/server.py),
  on `METRICS_PORT`/`HEALTH_PORT` (default 9090/8080).
- **M6+:** `/health` extended with last bar timestamp, feed status, metrics freshness.
- **M9:** Docker Compose publishes both ports; optional Prometheus sidecar.

### 10. Logging Modes

- **Regular:** INFO to stdout, human-readable text.
- **Debug:** DEBUG + JSON structured logs, controlled by `LOG_LEVEL`/`LOG_FORMAT`.
- Every order, signal, fill, and risk rejection gets a structured log event
  with `correlation_id` (see [`utils/logging.py`](../src/trading_platform/utils/logging.py)).
- Event bus publishes/handler invocations are logged at DEBUG.
- Periodic metrics summary logged at INFO when `log_summary_enabled: true`.
- Per-logger level overrides (e.g. quieting `uvicorn.access`) live in
  `config/logging.yaml`, applied on top of the global level.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Look-ahead bias in backtest | False confidence | Latency model delays fills to N+1 bar (M4); document OHLCV limitations |
| OHLCV-only fill approximation | Overstated strategy edge | Model spread/partials conservatively; document assumptions |
| ccxt API changes | Broken adapter | Adapter isolation; contract tests with recorded fixtures |
| Paper ≠ live fill quality | Strategy fails live | Shared `FillSimulator` between backtest and paper |
| Accidental live trading | Financial loss | Double env gate (`ENV=live` + `LIVE_TRADING_ENABLED=true`); paper is the default |
| Event handler ordering bugs | Wrong fill sequence | Explicit registration order in `container.py`; tests assert event sequence |
| Side-effect handler failure | Missed notification | Never let `NotificationHandler` block `ExecutionHandler` |
| Metrics overhead in hot path | Slower backtest | `TimedEventBus` is a thin wrapper; disable via `observability.enabled: false` |
| Over-engineering early | Slow delivery | In-memory sync bus only; no external broker until multi-process need |

## Roadmap

Full milestone breakdown (goals, deliverables, tests, acceptance criteria) is
tracked in the project plan and mirrored under [`docs/milestones/`](milestones/)
as each milestone lands.
