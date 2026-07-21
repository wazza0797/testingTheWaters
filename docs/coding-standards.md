# Coding Standards

These rules exist to keep the codebase modular and testable as it grows across
milestones. When in doubt, favor the simplest solution that doesn't block
future extension — avoid speculative abstraction.

## Architecture

- **Events:** All cross-module communication happens via typed domain events
  ([`domain/events/`](../src/trading_platform/domain/events/)) published on
  `IEventBus`. Never call a peer module (or its handler) directly.
- **Handlers:** One handler class per module, suffixed `Handler` (e.g.
  `StrategyHandler`, `MetricsHandler`). Subscriptions are registered **only**
  in [`container.py`](../src/trading_platform/container.py) — never inside a
  handler's own module.
- **Ports:** Domain interfaces are `typing.Protocol` classes living in
  `domain/ports/`. Infrastructure implementations are suffixed `Adapter`
  where they wrap a third-party integration (e.g. `BinanceAdapter`).
- **No globals:** Application state lives in objects constructed by
  `container.py` and passed via constructor injection. Configuration is read
  once (via `Settings`/`load_config`) at the composition root — never read
  ad hoc from deep inside business logic.

## Typing

- All public functions are typed. `uv run mypy src` runs with `strict = true`
  and must pass with zero errors before merging.
- Prefer `Protocol` over `ABC` for ports — structural typing keeps
  infrastructure decoupled from domain without inheritance coupling.
- Prefer `X | None` over `Optional[X]`; prefer builtin generics (`list[str]`,
  `dict[str, int]`) over `typing.List`/`typing.Dict`.

## Formatting & Naming

- **Formatting:** `ruff format` (line length 100). Run `uv run ruff format .`
  before committing; CI enforces `ruff format --check .`.
- **Linting:** `uv run ruff check .` must pass (rule set: `E`, `F`, `I`, `UP`,
  `B`, `SIM`, `N`, `C4` — see `pyproject.toml`).
- **Naming:** `snake_case` for modules/functions/variables; `PascalCase` for
  classes; `UPPER_SNAKE_CASE` for module-level constants.

## Domain Modeling

- **Immutability:** Domain models (`Bar`, `Signal`, `Order`, `Fill`, ...) and
  domain events are frozen dataclasses (`@dataclass(frozen=True, slots=True)`).
  Events additionally use `kw_only=True` so subclasses can add required
  fields after the base class's defaulted `correlation_id`/`timestamp`.
- **Money/quantities:** Always `decimal.Decimal`, never `float` — precision
  matters for tick/step size rounding, fee calculation, and P&L.
- **Validation:** Enforce invariants in `__post_init__`, raising a subclass of
  [`TradingPlatformError`](../src/trading_platform/domain/errors.py) (never a
  bare `Exception`/`ValueError` from domain code).

## Errors

- Domain exceptions live in `domain/errors.py`. Infrastructure code must catch
  third-party exceptions (ccxt, pyarrow, psutil, ...) at the adapter boundary
  and re-raise as a domain exception — application code never depends on a
  third-party exception type.
- Never swallow exchange/adapter errors silently; log and re-raise, or convert
  to a typed domain error.
- Exceptions from **side-effect handlers** (notifications, analytics) must be
  caught inside the handler — they must never propagate and block the
  critical path (strategy → risk → execution).

## Logging

- Use a module-level logger: `logger = logging.getLogger(__name__)`.
- Include structured context via `extra={...}` — `correlation_id`, `symbol`,
  `strategy` wherever applicable — never string-interpolate identifiers into
  the message itself.
- Configure logging exactly once, at process startup
  ([`utils/logging.py::configure_logging`](../src/trading_platform/utils/logging.py)).
  Library/module code must never call `logging.basicConfig()`.

## Metrics

- Never call `prometheus_client` directly outside
  `infrastructure/metrics/prometheus.py`. All other code depends on
  `IMetricsCollector`.
- Metric label sets are fixed per metric name (see the catalog in
  [`docs/architecture.md`](architecture.md)) — never introduce
  high-cardinality labels (order IDs, correlation IDs, prices).
- Handlers never instrument themselves manually; `TimedEventBus` and
  `MetricsHandler` do it automatically for every subscribed event.

## Tests

- Mirror `src/` under `tests/unit/` — one test file per module
  (`src/trading_platform/observability/handler.py` →
  `tests/unit/observability/test_handler.py`).
- Shared fixtures (synthetic bars, `FakeMetricsCollector`, instrument rules)
  live in [`tests/conftest.py`](../tests/conftest.py) — don't redefine them
  per test file.
- Strategy/indicator/backtest tests must run with **zero network access**.
  Tests that require a live exchange are marked `@pytest.mark.network` and
  excluded from the default `pytest` run (see `pyproject.toml`).
- New modules ship with tests in the same PR — untested code does not merge.

## Dependencies

- Only add a dependency to `pyproject.toml` when the milestone that needs it
  lands (e.g. `ccxt`/`pandas`/`pyarrow` are deferred to Milestone 1,
  `apscheduler` to Milestone 6) — keep install times and the dependency
  surface minimal.
