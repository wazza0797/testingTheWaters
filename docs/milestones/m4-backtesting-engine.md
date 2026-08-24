# Milestone 4 — Backtesting Engine

**Status:** Complete

## Goals

Replay cached historical bars through the real strategy -> risk -> execution
event pipeline with **realistic simulated fills** — spread, partial fills,
order latency, maker/taker fees, min order sizes, tick size, and precision
rules — and produce a trade log + equity curve. This is the first milestone
that actually *runs* a strategy end to end (M3 built the plugin contract but
deliberately left it unwired).

## Design Decisions (clarified with the user before implementation)

Four open questions were resolved with the user up front:

1. **Signal sizing policy: `fixed_fraction_equity`.** A `Signal` carries no
   quantity by design (see M0/M3); something has to turn it into a sized
   `Order`. Chose the simplest policy that unblocks backtesting: size every
   `BUY` as a fixed fraction of current equity (`config.backtest.position_size_pct`,
   default `1.0` = 100%), implemented as
   [`EquityFractionSizer`](../../src/trading_platform/risk/sizing.py). Real
   position-sizing strategies (Kelly, volatility-targeted, ...) are
   unscheduled future work behind the same `IRiskEngine` seam.
2. **Starting cash: `$10,000`.** `config.backtest.starting_cash`, a `Decimal`
   (quoted in YAML) so it round-trips exactly rather than going through a
   `float`.
3. **Portfolio tracking scope: an internal ledger, not a full `PortfolioHandler`.**
   [`BacktestLedger`](../../src/trading_platform/backtesting/ledger.py) is
   in-memory, backtest-run-scoped, driven directly by the backtest engine
   (no event-bus subscription, no persistence) — a disposable single-process
   run doesn't need any of that machinery yet. It exists solely to give
   `PassThroughRiskEngine` something to size against (`IPortfolioView`) and
   to give `BacktestEngine` an equity curve/fill history. Milestone 6 builds
   the real, event-driven, persisted `PortfolioHandler` for paper trading;
   this class is not a step toward it, just a scoped-down stand-in.
4. **CLI shape: a Typer subcommand.** `trading-platform backtest`, consistent
   with `download-data`/`serve` — not a separate script or a flag on an
   existing command.

Two structural gaps were also closed while wiring the event chain together:

5. **`Signal`/`Order` are price-free, but sizing and validation both need a
   reference price.** Rather than have `RiskHandler`/`ExecutionHandler` cache
   bars separately, `bar: Bar` was added to both `SignalGenerated` and
   `OrderApproved` — the triggering bar's close is the natural reference
   price, already available at the point each event is published, and this
   keeps `Signal`/`Order` themselves unchanged (still deliberately
   quantity/price-agnostic where that was always the design).
6. **`TradingLoop` is generic; `BacktestEngine` owns backtest-specific
   logic.** The project roadmap named `application/trading_loop.py` as the
   thing that "publishes `BarClosed`; drives all modes" (backtest replay
   *and* a future paper/live poll). Rather than let backtest-only concerns
   (draining `SimBroker`'s pending order queue before each bar; building an
   equity curve) leak into that shared class, `TradingLoop.run()` only
   publishes one `BarClosed` per bar and exposes `before_bar`/`after_bar`
   hooks — `BacktestEngine` supplies those hooks and owns everything
   backtest-specific. A future paper loop reuses `TradingLoop` unchanged.

## Delivered

### Risk engine (strategy -> risk boundary)

- [`risk/sizing.py`](../../src/trading_platform/risk/sizing.py) — `EquityFractionSizer`:
  `quantity = (equity * fraction) / price`, rounded down to `step_size` (never
  rounds up, so a sized order never exceeds `fraction * equity`).
- [`risk/engine.py`](../../src/trading_platform/risk/engine.py) — `PassThroughRiskEngine`:
  long-only (BTC/USDT is spot) — rejects a `BUY` while already in a position
  (no averaging/pyramiding) and a `SELL`/`CLOSE` while flat (nothing to
  close); otherwise sizes/approves. A `SELL`/`CLOSE` while holding always
  closes the *entire* position (no partial-reduce policy exists yet).
- [`risk/handler.py`](../../src/trading_platform/risk/handler.py) — `RiskHandler`:
  adapts `IRiskEngine` to the event bus (`SignalGenerated` in,
  `OrderApproved`/`RiskRejected` out), stamping the real `correlation_id`
  onto the engine's returned `Order` (the engine itself has no event to read
  it from).

### Execution (risk -> broker boundary)

- [`execution/order_validator.py`](../../src/trading_platform/execution/order_validator.py) —
  exchange-rule-level checks (`min_qty`, `min_notional`) against
  `InstrumentRules`, run *after* risk sizing/rounding — distinct from Risk's
  trading-policy-level rejections.
- [`execution/handler.py`](../../src/trading_platform/execution/handler.py) —
  `ExecutionHandler`: adapts `IBroker` to the event bus. Validates, forwards
  to the broker, and publishes `FillReceived` for anything returned
  synchronously (`SimBroker` never does — see below — but a future
  `PaperBroker`/`LiveBroker` might).

### Fill simulation (`backtesting/`)

- `models/spread_model.py`, `models/fee_model.py`, `models/latency_model.py`,
  `models/partial_fill_model.py` — the four independently-testable pieces of
  fill realism (see `docs/architecture.md` §7 for the full pipeline diagram
  and each model's specific behavior).
- `order_queue.py` — `OrderQueue`: tracks every order `SimBroker` is still
  working across bars (latency countdown, remaining quantity for partial
  fills).
- `fill_simulator.py` — `FillSimulator`: orchestrates one bar's fill attempt
  for one order (spread -> partial-fill cap -> fee); stateless, shared logic.
- `broker_sim.py` — `SimBroker` (`IBroker`): `submit_order` only enqueues
  (fills always have latency, so it always returns `[]`); `process_bar`
  (called once per bar by `BacktestEngine`, *before* that bar's strategy
  reaction) drains the queue and returns `(Order, Fill)` pairs.
- `ledger.py` — `BacktestLedger` (see Design Decision 3): applies fills,
  tracks weighted-average entry price on buys and realized P&L on sells,
  raises `PortfolioError` on an invalid sell (a bug upstream, never a normal
  runtime condition).

### Orchestration

- [`application/trading_loop.py`](../../src/trading_platform/application/trading_loop.py) —
  `TradingLoop` (see Design Decision 6): the one thing every run mode
  shares.
- `backtesting/engine.py` — `BacktestEngine`: replays bars via `TradingLoop`,
  drains `SimBroker` before each bar, applies/publishes its fills, records an
  equity-curve point after each bar, and assembles a `BacktestResult`.
- `backtesting/result.py` — `BacktestResult`/`EquityPoint`: the trade log,
  equity curve, and summary fields (`ending_equity`, `total_return_pct`) —
  deliberately does no further analysis (Sharpe, drawdown, win rate are
  Milestone 5's `analytics/`).

### Wiring

- `container.py::build_backtest_engine` — wires one backtest run's full
  strategy -> risk -> execution chain onto the container's event bus, given
  already-fetched `InstrumentRules`. Split out from `build_container` because
  it needs a cache round trip that `serve`/`download-data` shouldn't pay for.
- `main.py`'s `backtest` command (Design Decision 4) — resolves cached
  instrument rules and bars (never calls an exchange itself; errors out with
  a clear message telling the user to run `download-data` first), runs the
  engine, and prints a summary.
- `config/loader.py`/`config/backtest.yaml` — `BacktestConfig` gained
  `starting_cash`, `position_size_pct`; `backtest.yaml` now also selects the
  strategy to run (`strategy.path`/`strategy.params`) for the `backtest`
  command specifically.
- `domain/ports/strategy.py::StrategyContext` — `symbol`/`timeframe`/`params`
  changed from plain attributes to read-only `@property` members so a frozen
  dataclass (`DefaultStrategyContext`) structurally satisfies the `Protocol`
  under `mypy` (a plain attribute Protocol member requires a *settable*
  attribute on the implementer, which a frozen dataclass deliberately never
  has) — surfaced only once `container.py` actually constructed one.

## Addendum: Bugbot Findings, Fixed During Review

Bugbot review of the PR caught two real bugs before merge:

1. **(High) Risk ignored pending queued orders.** `PassThroughRiskEngine`
   decided BUY/SELL approval using only `BacktestLedger.position_for` —
   which reflects *filled* fills only. With `latency_bars >= 2`, repeated
   signals, or partial fills, a second BUY could be approved while an
   earlier BUY for the same symbol was still queued (not yet filled),
   violating the documented long-only/no-pyramiding policy; symmetrically, a
   SELL could be approved while a BUY was still partially filling, or two
   SELLs approved against the same holdings. **Fix:** added
   `IPendingOrderTracker` (`domain/ports/risk.py`) — `has_pending_order(symbol)`
   — implemented by `OrderQueue`/`SimBroker` (which already track exactly
   this) and injected into `PassThroughRiskEngine`, which now rejects *any*
   signal for a symbol while an order for it is still outstanding. Closes
   the gap for both directions with one check; regression tests in
   `tests/unit/risk/test_engine.py::TestPendingOrderGate`,
   `tests/unit/backtesting/test_order_queue.py::TestHasPendingOrder`, and
   `tests/unit/backtesting/test_broker_sim.py::TestHasPendingOrder`.
2. **(Medium) Partial fills ignored step size.** `FillSimulator` passed
   `PartialFillModel.fillable_quantity`'s volume-derived quantity straight
   through as `filled_qty`, unlike `EquityFractionSizer` (which always
   rounds to `step_size`). A volume-derived partial quantity could therefore
   violate the instrument's lot-size rule, or fall below `min_qty` in a way
   the `OrderValidator` would have rejected had it been submitted as a
   standalone order. **Fix:** `FillSimulator.simulate_fill` now rounds the
   partial-fill quantity down to `step_size` via `execution/precision.py::round_qty`,
   returning `None` (no fill this bar) if it rounds to zero — consistent
   with the existing "meaningless zero fill -> no fill" behavior. Regression
   tests in `tests/unit/backtesting/test_fill_simulator.py`.

A follow-up re-review (verifying the two fixes above) caught a third, smaller
issue:

3. **(Medium) `backtest --start`/`--end` crashed on common date input.**
   `datetime.fromisoformat("2024-01-01")` yields a **naive** datetime, but
   cached bars are always UTC-aware (`exchanges/binance/mapper.py`) —
   comparing the two inside `load_bars` raised a bare `TypeError`, which
   isn't a `TradingPlatformError`, so it bypassed the command's error
   handler and crashed the CLI instead of printing a clean message.
   **Fix:** both parsed values are now normalized with the existing
   `utils/time.py::to_utc` helper (already used elsewhere for exactly this),
   and a malformed `--start`/`--end` string (`ValueError`) is now caught and
   reported as a normal CLI error instead of a stack trace. Verified
   manually: `trading-platform backtest --start 2026-08-24` (naive input) now
   runs cleanly; `trading-platform backtest --start not-a-date` now exits 1
   with `Invalid --start/--end value: ...` instead of crashing.

Also caught and cleared by the paired Security Review: no medium+ issues
(safe YAML loading, no `eval`/`exec`/`pickle`, `Decimal` arithmetic
throughout, no secrets in new logging). It flagged the `BacktestLedger`
cash-sufficiency gap (already documented above) as backtest-fidelity debt
for a future milestone, not a vulnerability in this one.

## Verification

Ran an end-to-end smoke test with real downloaded BTC/USDT data (not just
synthetic bars in tests): `trading-platform download-data --days 30` followed
by `trading-platform backtest` against ~8,800 real hourly bars completed
without error, producing 339 fills and a full result summary — confirming the
CLI wiring, not just the individual units, works.

## Acceptance Criteria

| Criterion | Status | Evidence |
|-----------|--------|----------|
| Spread modeled | ✅ | `SpreadModel` — `tests/unit/backtesting/models/test_spread_model.py` |
| Partial fills modeled | ✅ | `PartialFillModel` + `OrderQueue` remaining-quantity carry-over — `tests/unit/backtesting/models/test_partial_fill_model.py`, `tests/unit/backtesting/test_order_queue.py`, `test_broker_sim.py::test_partial_fill_across_bars_eventually_completes` |
| Order latency modeled | ✅ | `LatencyModel` + `OrderQueue` — `tests/unit/backtesting/models/test_latency_model.py`, `test_broker_sim.py::TestProcessBar` (1-bar and 2-bar latency) |
| Maker/taker fees modeled | ✅ | `FeeModel` — `tests/unit/backtesting/models/test_fee_model.py` |
| Min order size / notional enforced | ✅ | `execution/order_validator.py` — `tests/unit/execution/test_order_validator.py` |
| Tick size / precision rules enforced | ✅ | `execution/precision.py` (M2), exercised by `risk/sizing.py::EquityFractionSizer` rounding and `order_validator.py` — `tests/unit/execution/test_precision.py` |
| No look-ahead bias | ✅ | An order reacting to bar N can only fill starting at bar N+1 — `BacktestEngine` drains `SimBroker` against bar N *before* publishing bar N's `BarClosed` (so the strategy never reacts to its own same-bar fill), and `latency_bars` delays further. Documented limitation (OHLCV-only intrabar path) in `docs/architecture.md` §7. |
| Signals sized into orders | ✅ | `EquityFractionSizer`/`PassThroughRiskEngine` — `tests/unit/risk/test_sizing.py`, `tests/unit/risk/test_engine.py` |
| Trade log + equity curve produced | ✅ | `BacktestResult.fills`/`.equity_curve` — `tests/unit/backtesting/test_engine.py` |
| CLI runs a full backtest | ✅ | `trading-platform backtest` — smoke-tested against real downloaded data (see Verification); `tests/integration/test_backtest_engine_integration.py` runs the real container + real `SmaCrossoverStrategy` config through a synthetic golden-cross/death-cross series with zero network access |
| Every module unit tested | ✅ | New/changed modules: `risk/sizing.py`, `risk/engine.py`, `risk/handler.py`, `execution/order_validator.py`, `execution/handler.py`, `backtesting/ledger.py`, `backtesting/models/*.py`, `backtesting/order_queue.py`, `backtesting/fill_simulator.py`, `backtesting/broker_sim.py`, `backtesting/engine.py`, `application/trading_loop.py` — all with dedicated `tests/unit/.../test_*.py` files |
| `uv run pytest -m "not network"` passes | ✅ | 410 passed (see Bugbot addendum below for the +15 added during review) |
| `uv run mypy src` / `ruff check .` / `ruff format --check .` | ✅ | all clean |

## Known Gaps (by design — later milestones)

- **No cash sufficiency check.** `BacktestLedger` never rejects a fill for
  insufficient cash. Sizing uses the signal-triggering bar's close, but the
  fill executes at a different bar's price plus spread and fees, so with
  `position_size_pct` at/near `1.0` cash can go slightly negative. A real
  position-sizing/risk engine (unscheduled future work) would size against a
  cash buffer instead of raw equity.
- **Single-symbol only.** `SimBroker`/`BacktestEngine` assume one
  symbol/timeframe per run (matching `config.trading.symbol`/`.timeframe`) —
  multi-symbol backtesting is unscheduled future work.
- **No performance analytics.** `BacktestResult` intentionally stops at the
  trade log and equity curve — Sharpe ratio, max drawdown, win rate, etc. are
  Milestone 5's `analytics/`.
- **`PassThroughRiskEngine` has no real risk rules** (max position size,
  drawdown halt, correlation limits) — see its docstring; these are
  unscheduled future work behind the same `IRiskEngine` seam.
- **OHLCV-only fill approximation** (no true order-book depth, intrabar path
  unknown) — see `docs/architecture.md` §7's Limitations note.
