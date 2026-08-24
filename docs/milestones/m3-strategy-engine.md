# Milestone 3 — Strategy Engine (Plugins)

**Status:** Complete

## Goals

Make strategies pluggable and entirely testable without exchange
connectivity: a stable `IStrategy` contract, an event-bus adapter
(`StrategyHandler`), a config-driven loader, and a reference SMA-crossover
strategy — with zero container/CLI wiring yet, since there is no
`TradingLoop`/`BacktestEngine` to drive a real trading mode until Milestone 4.

## Design Decisions (clarified with the user before implementation)

1. **Positions in `StrategyContext`.** No `Portfolio`/`PortfolioHandler`
   exists yet (Milestone 5). Added a minimal
   [`IPositionProvider`](../../src/trading_platform/domain/ports/portfolio.py)
   port and a `NullPositionProvider` stub (always flat) wired into
   `DefaultStrategyContext` by default. Strategies code against
   `ctx.position_for(...)` either way — the real, fills-backed implementation
   drops in at M5 with no strategy-facing change.
2. **`StrategyLoader` resolution.** Chose a `"module:ClassName"` dotted-path
   string read from config over a named built-in registry (the
   `IndicatorRegistry` pattern from M2). This makes "new file + config entry,
   no core changes" literally true — there's no registry function to edit
   per new strategy. `pyproject.toml`'s
   `[project.entry-points."trading_platform.strategies"]` group remains
   reserved for a genuine out-of-tree-package need later.
3. **Deferred `container.py`/CLI wiring.** `StrategyHandler` is not
   subscribed anywhere yet. `market_data/ingest.py` already publishes
   `BarClosed(mode="ingest")` on every `download-data` run — wiring a
   strategy handler into the shared bus now would silently generate trading
   signals during historical data downloads. Real wiring happens in
   Milestone 4 once `TradingLoop`/`BacktestEngine` exists and drives
   `BarClosed(mode="backtest")` for an actual run.
4. **Reference strategy signal semantics.** `SmaCrossoverStrategy` emits a
   signal **only on the bar where the cross actually happens** (golden cross
   → `BUY`, death cross → `SELL`), not on every bar the fast/slow
   relationship holds — otherwise a single sustained trend would emit the
   same signal every bar.

## Delivered

- `domain/ports/strategy.py` — `StrategyContext` fleshed out as a
  `Protocol` (not a concrete class), so the domain layer never has to import
  `pandas`/`indicators/` (the dependency rule in `docs/architecture.md`
  explicitly forbids `domain` → `pandas`). Exposes `symbol`, `timeframe`,
  `params`, `indicator(name, bars, **kwargs) -> float`, and
  `position_for(symbol) -> Position | None`.
- `domain/ports/portfolio.py` — `IPositionProvider`, a new small port for
  read-only position lookups (used by `StrategyContext`, will be reused by
  M5's `PortfolioHandler`).
- `strategies/context.py` — `DefaultStrategyContext` (concrete
  `StrategyContext`, backed by `indicators.IndicatorRegistry` and an injected
  `IPositionProvider`) and `NullPositionProvider` (the M3 stub).
- `strategies/handler.py` — `StrategyHandler`: subscribes to `BarClosed`
  filtered to its own symbol/timeframe, lazily calls `on_start` before the
  first matching bar, publishes `SignalGenerated` per returned `Signal`
  (reusing the triggering bar's `correlation_id` for end-to-end tracing), and
  exposes `stop()` to call `on_stop` (not tied to any event — no shutdown
  event exists until a real trading loop lands).
- `strategies/loader.py` — `load_strategy_class`/`instantiate_strategy`:
  resolve a strategy from a `"module:ClassName"` string via `importlib`,
  raising `StrategyError` (not a bare exception) for malformed paths, missing
  modules/classes, non-class attributes, or bad constructor params.
- `strategies/examples/sma_crossover.py` — `SmaCrossoverStrategy`: reference
  fast/slow SMA-crossover strategy. Maintains its own bounded bar buffer
  (`on_bar` only ever receives one new bar), calls `ctx.indicator("sma", ...)`
  for both periods each bar, and emits `BUY`/`SELL` only on the actual
  crossing bar.
- `config/loader.py` — new `StrategyConfig` (`path`, `params`) section on
  `AppConfig`; `config/default.yaml` documents (commented out) how to select
  the bundled reference strategy. Unread by any runner until M4.
- `tests/conftest.py` — added a shared `FakeEventBus`/`fake_event_bus`
  fixture (previously duplicated ad hoc in `test_ingest.py`; deduplicated
  per `docs/coding-standards.md`'s "don't redefine shared fixtures" rule).
- 48 new unit tests across `tests/unit/strategies/` (context, handler, loader,
  reference strategy) plus 2 new `config/loader.py` tests for the new
  `StrategyConfig` section. Full suite: **246 passed** (up from 198 at M2).

## Acceptance Criteria

| Criterion | Status | Evidence |
|-----------|--------|----------|
| Strategy runs in isolation with synthetic data | ✅ | Every `strategies/` test constructs `Bar`s via the `make_bar` fixture and calls `on_bar`/`ctx.indicator` directly — zero event bus, exchange, or network access. `grep` for `ccxt\|exchanges\.\|execution` inside `src/trading_platform/strategies/` and `tests/unit/strategies/` returns only docstring mentions, never an actual import. |
| Adding a new strategy = new file + config entry (no core changes) | ✅ | Empirically verified (not just asserted): wrote a throwaway `AlwaysFlatStrategy` to a new file, resolved and instantiated it via `instantiate_strategy("trading_platform.strategies.examples._verify_new_strategy:AlwaysFlatStrategy")` with **zero edits** to `loader.py`, `handler.py`, `context.py`, or `container.py` — then deleted the file (it was only a verification artifact, not a shipped deliverable). |
| Unit: SMA crossover generates buy/sell on known cross patterns | ✅ | `tests/unit/strategies/examples/test_sma_crossover.py` — hand-computed SMA(2)/SMA(3) table for an 11-bar series, asserting a `BUY` at exactly index 5 (golden cross) and `SELL` at exactly index 7 (death cross), and **no** signal at any of the other 9 indices, including the flat plateaus where fast == slow exactly. |
| Unit: `StrategyHandler` publishes correct events on bar sequence | ✅ | `tests/unit/strategies/test_handler.py` — symbol/timeframe filtering, one-time `on_start`, per-signal `SignalGenerated` publication, `correlation_id` propagation from the triggering `BarClosed`, and `stop()`/idempotency. Plus two end-to-end tests through a real `InMemoryEventBus` (not just `handler.handle(...)` calls) — one with a scripted double, one with the real `SmaCrossoverStrategy` — publishing `BarClosed` and asserting `SignalGenerated` comes out the other side. |
| Unit: loader resolves strategy from config | ✅ | `tests/unit/strategies/test_loader.py` (direct path resolution + error cases) and `tests/unit/config/test_loader.py` (YAML → `AppConfig.strategy.path`/`.params` → `instantiate_strategy`). |
| **Bugbot finding, fixed during review** | ✅ | `instantiate_strategy` only wrapped constructor `TypeError` in `StrategyError` — a strategy raising a bare `ValueError` for semantically-invalid params (e.g. `SmaCrossoverStrategy`'s `fast_period >= slow_period` check) leaked past the loader's documented error contract. Now catches `(TypeError, ValueError)`; added a regression test. |
| Zero network/exchange imports in strategy tests | ✅ | No test in `tests/unit/strategies/` uses `pytest.mark.network`, a `FakeExchangeAdapter`, or imports anything from `exchanges/`/`execution/`. |
| `uv run pytest -m "not network"` passes | ✅ | 255 passed, 2 deselected (after the compatibility-safeguards addendum below) |
| `uv run mypy src` / `ruff check .` / `ruff format --check .` | ✅ | all clean |

## Addendum: Compatibility Safeguards (follow-up, same milestone)

After the initial M3 merge, the user asked a pointed question: *if I keep
asking for more strategies, how do we make sure distinct strategies are
always compatible with the rest of the system?* That's a fair challenge to
the honesty of the original "Strategy runs in isolation" acceptance
criterion — passing tests for *one* strategy doesn't guarantee the *next*
one will be well-behaved. Four gaps were identified and closed in a
follow-up PR before any more strategies get added:

1. **Identity collisions.** `SmaCrossoverStrategy` hardcoded
   `strategy_name="sma_crossover"` as a module constant. Two instances of
   the same class with different params (a fast 5/20 crossover and a slow
   20/60 crossover) would have been indistinguishable in every metric, log,
   and signal downstream. **Fix:** identity moved out of the strategy
   entirely and into `StrategyHandler`, which now takes a required `name`
   and overwrites `Signal.strategy_name` with it before publishing — no
   strategy author needs to remember to set (or coordinate) a unique name.
   `strategies/loader.py::describe_strategy(path, symbol, params)` derives
   that name automatically and deterministically from the class name, the
   traded symbol, and the parameters (e.g.
   `"SmaCrossoverStrategy[BTC/USDT](fast_period=5,slow_period=20)"`), params
   sorted by key for determinism. The symbol was added after a follow-up
   discussion with the user, who wanted the instrument obvious at a glance
   too — without it, the same class+params running on two different
   symbols would have looked identical. Two differently-configured
   instances (by params, symbol, or both) now get distinct names with zero
   manual bookkeeping and no risk of a human picking colliding names.
2. **No validation of what a strategy hands back.** `StrategyHandler`
   published whatever `Signal`s `on_bar` returned, unchecked.
   **Fix:** it now validates every returned signal's `symbol` matches the
   triggering bar's `symbol` before publishing any of them for that bar
   (all-or-nothing per bar), raising `StrategyError` on a mismatch instead of
   silently forwarding a wrong-symbol signal toward Risk/Execution.
3. **The dynamically-loaded path bypassed static typing entirely.**
   `StrategyLoader` used `cast(type[IStrategy], strategy_cls)` — a promise to
   mypy, not a runtime check. A strategy missing `on_stop` (say) wouldn't
   fail until `StrategyHandler` called it mid-run. **Fix:** `IStrategy` is
   now `@runtime_checkable`; `instantiate_strategy` checks
   `isinstance(strategy, IStrategy)` right after construction and raises a
   clear `StrategyError` immediately if the shape doesn't match.
4. **No shared conformance suite.** Determinism, no-crash-on-a-single-bar,
   and lifecycle-hook checks existed only in `SmaCrossoverStrategy`'s own
   bespoke tests — nothing forced the *next* strategy's tests to cover the
   same ground. **Fix:** added
   `tests/unit/strategies/conformance.py::assert_strategy_conforms`, a
   reusable helper any strategy's test file can call alongside its own
   algorithm-specific assertions. `test_sma_crossover.py` now calls it.

Also added: `tests/unit/strategies/_fixtures.py` (a deliberately incomplete
strategy used only to test the new runtime shape check) and regression tests
for all four fixes. Full suite grew from 246 → 255 passed; `mypy`/`ruff`
remain clean.

## Known Gaps (by design — later milestones)

- `StrategyHandler`/`StrategyLoader` are **not wired into `container.py` or
  the CLI** — see Design Decision 3. There is no `backtest`/`paper` command
  that actually runs a strategy yet; that lands in M4 (backtest engine) and
  M6 (paper trading), reusing these components unchanged.
- `NullPositionProvider` always reports flat — no real position tracking
  exists until M5's `PortfolioHandler`.
- Indicator values are recomputed from a strategy's full bounded buffer on
  every bar (no incremental/streaming computation) — same accepted trade-off
  as M2's `IndicatorRegistry`; buffers here are capped at `slow_period + 1`
  bars, so the recomputation cost stays tiny regardless of backtest length.
- `SmaCrossoverStrategy` is a reference/demonstration strategy, not a
  strategy claimed to have any trading edge — it exists to prove the plugin
  contract end-to-end.
