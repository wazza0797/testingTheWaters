# Trading Platform

A modular, extensible algorithmic trading platform — crypto-first (BTC/USDT on
Binance), paper trading before live, designed from day one to support
multiple exchanges, strategies, and asset classes.

> **Status:** Milestones 0–6 complete (including paper trading with persisted
> virtual portfolio). Live execution and notifications are still ahead —
> see [`docs/milestones/`](docs/milestones/) and the project plan.

## Non-Goals (for now)

- Not a "one-click trading bot" — no strategy logic is baked into core modules.
- Not multi-exchange/multi-asset yet — Binance + BTC/USDT is the first vertical
  slice; the architecture is exchange/asset-agnostic by design (see
  [`docs/architecture.md`](docs/architecture.md)).
- Not live-trading-ready — live execution is gated behind paper trading
  validation and explicit environment confirmation (Milestone 8).

## Architecture at a Glance

Strategies emit signals; a risk engine approves or rejects them; execution
fills orders. Every module communicates through a typed, in-process **event
bus** — no module calls another directly. See
[`docs/architecture.md`](docs/architecture.md) for the full diagram, dependency
rules, and design decisions (event bus, realistic backtest fill simulation,
operational metrics from day one).

```
Bar closed -> Strategy -> Signal -> Risk -> Order -> Execution -> Fill
                                              |
                                   Portfolio / Analytics / Notifications
                                        (all via the event bus)
```

## Quick Start

Requires [`uv`](https://docs.astral.sh/uv/) and Python 3.11+ (uv will install
the pinned interpreter automatically).

```bash
# Install dependencies (creates .venv automatically)
uv sync

# Run the test suite
uv run pytest

# Type-check (strict mode)
uv run mypy src

# Lint
uv run ruff check .

# Start the observability server (/health on :8080, /metrics on :9090)
uv run trading-platform serve
```

Then in another terminal:

```bash
curl localhost:8080/health
curl localhost:9090/metrics
```

## Configuration Reference

Two sources of configuration, deliberately kept separate:

| Source | Contents | Location |
|--------|----------|----------|
| YAML | Symbols, timeframes, strategy params, backtest/observability tuning | [`config/*.yaml`](config/) |
| Environment variables | Secrets (API keys, Telegram token) + a few deployment toggles | `.env` (see [`.env.example`](.env.example)) |

Never commit real secrets. `.env` is gitignored; only `.env.example` (with
placeholder values) is tracked.

## Writing a Strategy (Plugin Guide)

Strategies implement the `IStrategy` protocol
([`domain/ports/strategy.py`](src/trading_platform/domain/ports/strategy.py))
and must be fully testable with synthetic bars — no imports from `exchanges/`,
`execution/`, or `ccxt`. Add a new strategy as a new file plus a
`"module:ClassName"` config entry, no changes to any core module — see
[`docs/milestones/m3-strategy-engine.md`](docs/milestones/m3-strategy-engine.md)
for the full design rationale and
[`src/trading_platform/strategies/examples/sma_crossover.py`](src/trading_platform/strategies/examples/sma_crossover.py)
for a worked reference implementation.

## Backtesting

Replays cached historical bars through the real strategy -> risk ->
execution pipeline with realistic simulated fills (spread, latency, partial
fills, maker/taker fees, exchange precision rules) — see
[`docs/milestones/m4-backtesting-engine.md`](docs/milestones/m4-backtesting-engine.md)
for the full design.

```bash
# 1. Download historical data + instrument rules first (never done by `backtest` itself)
uv run trading-platform download-data --days 365
# other timeframes: --timeframe 4h / 15m / 1d (data is stored per timeframe)

# 2. Select a strategy and tune fill-simulation parameters in config/backtest.yaml,
#    then run the backtest (optional overrides — defaults come from config)
uv run trading-platform backtest
uv run trading-platform backtest --timeframe 4h --start 2024-01-01 --end 2025-01-01
uv run trading-platform backtest --symbol BTC/USDT --timeframe 1d
uv run trading-platform backtest --report json   # human summary + JSON PerformanceReport

# 3. Walk-forward grid search (Milestone 4.5 Phase C) — configure
#    validation.walk_forward in config/backtest.yaml first
uv run trading-platform walk-forward

# 4. Paper trade (Milestone 6) — polls live closed candles with virtual cash;
#    state persists under DATA_DIR/paper_state.json (Ctrl+C to stop)
uv run trading-platform paper
```

Prints a performance report (round-trips, total return, max drawdown, Sharpe,
win rate, profit factor, significance flags, calendar/market regime tables,
buy-and-hold benchmark) after each run. With hold-out validation enabled,
IS and OOS each get their own report. Both `download-data` and `backtest` warn
(without failing) if the loaded bars have any timestamp gaps, since a silent
gap in historical data can otherwise bias results — see
`docs/architecture.md`'s Limitations/Risks sections for this and other
backtesting biases worth being aware of (overfitting/data-snooping,
point-in-time instrument rules, static spread assumptions).

`--symbol` / `--timeframe` override `config/default.yaml` for that run only
(you still need matching cached data from `download-data`). `--start` /
`--end` bound the calendar window.

## Paper vs Live Trading Safety

Paper trading (Milestone 6) requires no exchange API keys and never calls
order-placement methods on the exchange adapter. Live trading (Milestone 8) is
double-gated: `ENV=live` **and** `LIVE_TRADING_ENABLED=true` must both be set
explicitly, or the process refuses to start (see
[`Settings.require_live_trading_confirmed`](src/trading_platform/config/settings.py)).

## Docker Deployment

Containerized deployment lands in Milestone 9. The observability server
(`/health`, `/metrics`) is designed from Milestone 0 to run identically
in-container as it does locally.

## Development Setup

```bash
uv sync              # install deps + dev tools (pytest, mypy, ruff)
uv run pytest        # unit tests (network tests excluded by default)
uv run pytest -m network  # include tests that hit a live exchange
uv run mypy src       # strict type checking
uv run ruff check .   # lint
uv run ruff format .  # format
```

See [`docs/coding-standards.md`](docs/coding-standards.md) and
[`docs/git-workflow.md`](docs/git-workflow.md) before contributing.

## Roadmap

Full milestone breakdown (goals, deliverables, tests, acceptance criteria) is
tracked in the project plan and mirrored under
[`docs/milestones/`](docs/milestones/) as each milestone lands.

**Next up (planned, not yet implemented):**

| Milestone | Focus |
|-----------|-------|
| M7+ | Notifications, live trading, Docker |
