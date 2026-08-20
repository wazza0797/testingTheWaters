# Milestone 1 — Historical Data Download

**Status:** Complete

## Goals

Download BTC/USDT OHLCV bars from Binance, fetch and cache exchange
instrument rules, persist bars to Parquet with idempotent incremental
updates, and expose a working `download-data` CLI command.

## Delivered

- `pyproject.toml` — added `ccxt` (exchange adapter) and `pyarrow` (Parquet
  I/O); `pandas`/`apscheduler` remain deferred to the milestones that need
  them.
- `exchanges/binance/mapper.py` — pure functions mapping ccxt OHLCV rows and
  `market` metadata dicts to `Bar`/`InstrumentRules`. Assumes ccxt's
  `TICK_SIZE` precision mode (Binance's default); falls back to parsing raw
  `info.filters` for `min_notional` when ccxt's normalized `limits.cost.min`
  is absent.
- `exchanges/binance/adapter.py` — `BinanceAdapter` implementing
  `IExchangeAdapter` against a small internal `_CcxtExchange` Protocol (not
  `ccxt.binance` directly), so unit tests inject a plain fake with no network
  or real ccxt calls. `fetch_ohlcv`/`fetch_instrument_rules` are retried on
  `ccxt.NetworkError`; `place_order`/`cancel_order`/`get_balance` raise
  `NotImplementedError` until live trading lands in M8.
- `execution/precision.py` — `round_price`/`round_qty` (round-down-by-default
  to tick/step size) and `meets_min_qty`/`meets_min_notional` guards, built on
  `InstrumentRules`. Full order validation/rejection wiring is M4.
- `market_data/repository/layout.py` + `market_data/repository/parquet.py` —
  `ParquetMarketDataRepository` implementing `IMarketDataRepository`,
  partitioned `{root}/ohlcv/{exchange}/{symbol}/{timeframe}/YYYY-MM.parquet`.
  Prices/volumes are stored as decimal strings (never `float64`) for exact
  round-tripping; writes are merge-by-timestamp (idempotent, no duplicates)
  and atomic (write-to-temp + rename).
- `market_data/instrument_rules_cache.py` — `InstrumentRulesCache`, a plain
  JSON file cache at `{root}/instruments/{exchange}/{symbol}.json`.
- `market_data/ingest.py` — `DataIngestService.sync()`: resumes from
  `IMarketDataRepository.latest_timestamp`, paginates until a short page
  signals "no more history", and publishes `BarClosed(mode="ingest")` per new
  bar so the existing `MetricsHandler` records `trading_bars_processed_total`
  with zero ingest-specific metrics code. A `_MAX_PAGES` safety cap guards
  against a misbehaving adapter that never returns a short page.
- `container.py` wires `BinanceAdapter`, `ParquetMarketDataRepository`,
  `InstrumentRulesCache`, and `DataIngestService`; `main.py`'s
  `download-data` command resolves symbol/timeframe from CLI flags or
  `config/default.yaml` (`trading.symbol`/`trading.timeframe`), fetches and
  caches instrument rules on first run, then syncs bars for `--days` back.

## Acceptance Criteria

| Criterion | Status |
|-----------|--------|
| Download 1 year of 1h BTC/USDT bars to Parquet | ✅ (verified manually against live Binance for a shorter window; pagination logic is exercised in unit tests) |
| Instrument rules for BTC/USDT cached with correct tick/step size, min_notional, maker/taker fees | ✅ (verified manually + `test_mapper.py`) |
| Data ingest records `trading_bars_processed_total{mode="ingest"}` | ✅ (`BarClosed` publish path, unit-tested in `test_ingest.py`) |
| Re-run download appends only new bars (no duplicates) | ✅ (verified manually + `test_parquet_repository.py`) |
| Repository load returns chronologically ordered bars | ✅ |
| Zero Binance-specific fields/quirks outside `exchanges/binance/` | ✅ (only the composition root wires the concrete `BinanceAdapter`, and `exchange: str = "binance"` is a generic default value, not Binance-specific logic) |
| `uv run pytest -m "not network"` passes | ✅ (154 tests) |
| `uv run mypy src` passes with strict mode | ✅ |

## Known Gaps (by design — later milestones)

- No indicator engine yet — bars are stored but not yet consumed by anything
  besides the metrics pipeline (M2).
- `execution/precision.py` has rounding/threshold helpers only; the full
  `OrderValidator` that rejects orders and publishes `OrderRejected` lands in
  M4 alongside the backtest fill simulator.
- `place_order`/`cancel_order`/`get_balance` on `BinanceAdapter` raise
  `NotImplementedError` — gated until M8 (live trading).
