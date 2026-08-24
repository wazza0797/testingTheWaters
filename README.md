# Trading Platform

A modular, extensible algorithmic trading platform — crypto-first (BTC/USDT on
Binance), paper trading before live, designed from day one to support
multiple exchanges, strategies, and asset classes.

> **Status:** Milestones 0–3 complete (foundation, historical data download,
> indicator engine, pluggable strategy engine). No risk, execution, or
> backtesting logic exists yet, and no strategy is wired into a running
> loop — see [`docs/milestones/`](docs/milestones/) and the project plan for
> the full roadmap.

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
`execution/`, or `ccxt`. This lands in Milestone 3; see
[`docs/milestones/`](docs/milestones/) for details once available.

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
