# Milestone 6 — Paper Trading

**Status:** Complete

**Depends on:** Milestone 4 (fill simulation), Milestone 5 (`AnalyticsHandler`)

**Unblocks:** Milestone 7 (notifications), Milestone 8 (live trading)

## Goals

Run the strategy loop against **live closed candles** with **virtual cash**,
using the same fill realism as backtests (spread, fees, latency, partials),
and **persist** cash/positions so a restart continues the same paper session.

## Design decisions (confirmed)

1. **Persistence:** JSON file under `data_dir` (default `paper_state.json`)
2. **Fills:** Shared `FillSimulator` + order queue (same models as backtest)
3. **Bar source:** Poll exchange OHLCV for the latest *fully closed* candle
4. **Out of scope:** real orders, multi-symbol, notifications, UI

## Components

| Component | Path | Role |
|-----------|------|------|
| Portfolio book | `portfolio/book.py` | Cash/position apply_fill (shared with backtest ledger) |
| JSON store | `portfolio/persistence.py` | Load/save paper state |
| Handler | `portfolio/handler.py` | `FillReceived` → book + persist; marks on `BarClosed` |
| Paper broker | `execution/paper_broker.py` | Queue + `process_bar` like `SimBroker` |
| Feed | `market_data/feed.py` | `PollingMarketDataFeed.poll_latest_closed_bar` |
| Paper loop | `application/paper_loop.py` | Poll → process fills → `BarClosed` → sleep |
| CLI | `main.py` `paper` | Wire session and run until interrupted |
| Config | `PaperConfig` + `config/paper.yaml` | Cash, poll interval, state file, strategy |

## Acceptance criteria

- Paper loop processes only closed bars (no look-ahead on forming candle)
- Fills use configured spread/fee/latency models
- Restart loads prior cash/positions from JSON
- Strategy/risk see live positions via `IPortfolioView`
- `uv run pytest -m "not network"` / mypy / ruff green
