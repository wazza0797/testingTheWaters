# Milestone 0 — Project Foundation

**Status:** Complete

## Goals

Reproducible dev environment, CI skeleton, domain ports defined, event bus
infrastructure, operational metrics from day one, DI composition root,
logging/config infrastructure.

## Delivered

- `pyproject.toml` (uv-managed, Python 3.11+ pinned via `.python-version`)
  with dev deps (pytest, mypy, ruff) and M0 runtime deps (`prometheus_client`,
  `psutil`, `pydantic`, `pydantic-settings`, `fastapi`, `uvicorn`, `typer`,
  `pyyaml`, `python-dotenv`). `ccxt`/`pandas`/`pyarrow`/`apscheduler`
  deliberately deferred to the milestones that need them.
- Domain models: `Bar`, `Signal`, `Order`, `Fill`, `Position`, `Portfolio`,
  `InstrumentRules` — frozen dataclasses using `Decimal` for all
  price/quantity fields.
- Domain events: `BarClosed`, `FeedStalled`, `SignalGenerated`,
  `OrderApproved`, `RiskRejected`, `FillReceived`, `OrderRejected`,
  `ErrorOccurred`, `Heartbeat` — frozen, `kw_only=True`, with
  `correlation_id`/`timestamp` from a shared `Event` base.
- Domain ports (Protocols): `IEventBus`, `IEventHandler`, `IMetricsCollector`,
  `IExchangeAdapter`, `IMarketDataRepository`, `IMarketDataFeed`, `IStrategy`,
  `IRiskEngine`, `IExecutionEngine`, `IBroker`, `INotifier`.
- `InMemoryEventBus` (sync, deterministic, registration-order dispatch) and
  `TimedEventBus` (records per-handler latency/error metrics transparently).
- `PrometheusMetricsCollector` (lazy per-name metric creation behind
  `IMetricsCollector`), `MetricsHandler` (event → counter translation),
  `SystemMonitor` (CPU/memory/uptime gauges via psutil, resilient to poll
  failures).
- `observability/server.py` (FastAPI `GET /health` + `GET /metrics`) and
  `observability/summary.py` (`SummaryTrackingMetricsCollector` +
  `PeriodicSummaryLogger` — periodic structured rate/latency log).
- `container.py` composition root wiring `TimedEventBus` + `MetricsHandler`
  subscriptions; `main.py` Typer CLI (`serve`, `version`, plus
  `download-data`/`backtest`/`paper` stubs pointing at their milestones).
- Pydantic `Settings` (env-backed secrets/ops toggles) + `config/loader.py`
  (`AppConfig` from deep-merged YAML) + `config/*.yaml`.
- Structured logging (`utils/logging.py`: text/JSON formatters, per-logger
  overrides from `config/logging.yaml`).
- `README.md`, `docs/architecture.md`, `docs/coding-standards.md`,
  `docs/git-workflow.md`.
- GitHub Actions CI: `ruff check`, `ruff format --check`, `mypy --strict`,
  `pytest --cov` on every push/PR.

## Acceptance Criteria

| Criterion | Status |
|-----------|--------|
| `uv run pytest` passes | ✅ (99 tests) |
| `uv run mypy src` passes with strict mode | ✅ |
| `uv run trading-platform --help` shows CLI | ✅ |
| `curl localhost:9090/metrics` exposes `trading_process_uptime_seconds` and handler histograms | ✅ (verified manually; `Heartbeat` events exercise the handler-latency pipeline end-to-end since no strategy/risk/execution handlers exist yet) |
| Periodic metrics summary appears in logs when enabled | ✅ (`PeriodicSummaryLogger`, unit-tested; default 60s interval in `config/observability.yaml`) |
| No secrets in repo; `.env.example` documents required vars | ✅ |

## Coverage

`domain/`, `config/`, `infrastructure/event_bus/`, and `observability/` are at
or near 100% branch coverage. `domain/ports/*` (pure `Protocol` declarations)
and `main.py` (CLI/process glue, verified manually via `curl`) are
intentionally excluded from the 80% target — see `docs/coding-standards.md`.

## Known Gaps (by design — later milestones)

- No real strategy/risk/execution handlers yet — `StrategyContext` is a
  placeholder `Protocol` fleshed out in M3.
- No exchange adapter, market data repository, or `InstrumentRules` fetching
  — M1.
- `application/trading_loop.py` exists as a package stub only — implemented
  in M4 alongside the backtest fill simulator.
