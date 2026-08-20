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
- `market_data/instrument_rules_cache.py` — `InstrumentRulesCache`, a JSON
  file cache at `{root}/instruments/{exchange}/{symbol}.json` with a
  `cached_at` timestamp; entries older than `max_age_hours` (default 24 — the
  "refreshed on startup or daily" deliverable) are treated as missing, so
  callers transparently re-fetch from the exchange.
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

Each of the six milestone-defined acceptance criteria was independently
verified against live Binance (not just unit-tested), with evidence:

| Criterion | Status | Evidence |
|-----------|--------|----------|
| Download 1 year of 1h BTC/USDT bars to Parquet | ✅ | `download-data --days 365` → 8,760 bars persisted across 13 monthly Parquet partitions (`data/ohlcv/binance/BTC-USDT/1h/2025-08.parquet` … `2026-08.parquet`), spanning `2025-08-20T18:00Z` → `2026-08-20T18:00Z` with **zero gaps** (checked every consecutive pair is exactly 1h apart) and zero duplicates. `data/` is gitignored/local-only, per the deliverable. |
| Instrument rules for BTC/USDT cached with correct tick/step size, min_notional, maker/taker fees | ✅ | Cross-checked the cached `data/instruments/binance/BTC-USDT.json` against an independent, direct `ccxt.binance().load_markets()` call (not via our own adapter code): `tick_size=0.01`, `step_size=0.00001`, `min_qty=0.00001`, `min_notional=5.0`, `maker=taker=0.001` — all match exactly, including Binance's current `NOTIONAL` filter (renamed from `MIN_NOTIONAL`), confirming the mapper's fallback path. |
| Data ingest records `trading_bars_processed_total{mode="ingest"}` | ✅ | Built a real container against an isolated data dir, ran `data_ingest_service.sync(...)` for a live 2-day window (48 bars), then rendered the actual Prometheus registry: `trading_bars_processed_total{mode="ingest",symbol="BTC/USDT"} 48.0` — confirms the full event bus → `MetricsHandler` → Prometheus path, not just that the event gets published. |
| Re-run download appends only new bars (no duplicates) | ✅ | Re-ran `download-data --days 365` against the already-populated 8,761-bar dataset: `0 new bar(s) persisted`, count/sort/uniqueness unchanged afterward. |
| Repository load returns chronologically ordered bars | ✅ | `load_bars()` on the full 8,761-bar dataset returns timestamps in strictly ascending order. |
| Zero Binance-specific fields/quirks outside `exchanges/binance/` | ✅ (with one honest nuance below) | See note. |
| `uv run pytest -m "not network"` passes | ✅ | 158 tests |
| `uv run mypy src` / `ruff check` / `ruff format --check` | ✅ | all clean |

**Nuance on the Binance-isolation criterion:** taken literally, `container.py`
(the composition root) does import and instantiate `BinanceAdapter` by name —
this is unavoidable and correct under Dependency Injection: *something* has
to wire the concrete implementation, and the composition root is explicitly
the one place allowed to do so (same as how it already wires
`InMemoryEventBus`, `PrometheusMetricsCollector`, etc.). What the criterion
actually guards against — Binance-specific *fields, quirks, or parsing
logic* leaking into business/domain code — is genuinely zero outside
`exchanges/binance/`; `ParquetMarketDataRepository`'s `exchange` parameter
has no default value (must be passed explicitly), so even that is fully
exchange-agnostic by construction. `config/settings.py`'s
`binance_api_key`/`binance_api_secret` fields are a pre-existing M0 artifact
(unused until M8) that will need to become exchange-parameterized once a
second exchange is added — flagged here rather than silently ignored.

**Also fixed while verifying:** the instrument rules cache had no staleness
policy — a cached file was trusted forever, contradicting the "refreshed on
startup or daily" deliverable. Added a `cached_at` timestamp with a
`max_age_hours` (default 24) expiry; verified end-to-end that a fresh cache
is reused (no re-fetch) and a stale/missing-field cache correctly triggers a
re-fetch.

## Known Gaps (by design — later milestones)

- No indicator engine yet — bars are stored but not yet consumed by anything
  besides the metrics pipeline (M2).
- `execution/precision.py` has rounding/threshold helpers only; the full
  `OrderValidator` that rejects orders and publishes `OrderRejected` lands in
  M4 alongside the backtest fill simulator.
- `place_order`/`cancel_order`/`get_balance` on `BinanceAdapter` raise
  `NotImplementedError` — gated until M8 (live trading).
