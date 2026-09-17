# Milestone 8a Phase C — IG Markets demo adapter (+ live scaffold)

**Status:** Complete (demo path); live trading still gated

**Depends on:** Milestone 8a Phase A+B (DemoBroker / factory), basic short support
in risk/portfolio (`InstrumentRules.allows_short`)

## Goals

- Second venue behind the same `IExchangeAdapter` + `DemoBroker` path as Binance demo
- **Demo only for usable trading:** `demo-api.ig.com` + `IG_DEMO_*` credentials
- **Live scaffolded but refused:** `IgAdapter.for_live` + `IG_API_*` exist; factory
  raises on `ENV=live` (same barrier as Binance live today)
- Spot stays long-only (`allows_short=False`); IG CFDs set `allows_short=True`

## Demo vs live (IG)

| | Demo | Live |
|--|------|------|
| Host | `https://demo-api.ig.com/gateway/deal` | `https://api.ig.com/gateway/deal` |
| Credentials | `IG_DEMO_*` | `IG_API_*` (not interchangeable) |
| This milestone | Working | Scaffold only; factory refuses |

## How to run demo

1. Create an IG **demo** account + API key (IG Labs).
2. Set in `.env`: `ENV=demo`, `IG_DEMO_API_KEY`, `IG_DEMO_USERNAME`,
   `IG_DEMO_PASSWORD`, optional `IG_DEMO_ACCOUNT_ID`.
3. Choose a config overlay:
   - Edit `config/demo.yaml` (comment out Binance, uncomment IG), **or**
   - Pass `--overlay` so research configs drive the sleeve without editing
     `demo.yaml` (DemoConfig defaults apply when the overlay omits `demo:`).
4. Pipeclean first (venue-agnostic smoke):

```bash
uv run trading-platform demo-smoke
# or: uv run trading-platform demo-smoke --symbol CS.D.EURUSD.MINI.IP
# Connors US500 sleeve (cash CFD epic IFM — not DAILY/DFB):
uv run trading-platform download-data --overlay ig-us500
uv run trading-platform demo-smoke --overlay ig-us500
# short open (IG / allows_short only): --side sell
```

5. **Live integration suite** (hits `demo-api.ig.com`; excluded from CI).
   Real clients pace ~3s between requests — expect several minutes:

```bash
# Full suite (one-liner — dealing + reads; places demo open/close)
IG_DEMO_INTEGRATION=1 IG_DEMO_EPIC=CS.D.GBPEUR.CFD.IP uv run pytest -m network tests/integration/test_ig_adapter_network.py -v

# Read-only: session, accounts, markets, prices, rules, seed — no orders
uv run pytest -m network tests/integration/test_ig_adapter_network.py -k "not Dealing and not DemoSmoke" -v
```

6. Then run the full loop:

```bash
uv run trading-platform demo
# Connors US500 (sparse daily signals; strategy warms from cache/IG history):
uv run trading-platform demo --overlay ig-us500
```

## API matrix (what we call vs Labs reference)

| Call | Method | Version | Path | Notes |
|------|--------|---------|------|-------|
| Login | POST | 2 | `/session` | CST + X-SECURITY-TOKEN from response headers |
| Switch account | PUT | 1 | `/session` | Only if `IG_DEMO_ACCOUNT_ID` / live account id set |
| Accounts | GET | 1 | `/accounts` | Cash / available |
| Open positions | GET | 2 | `/positions` | Nested `position` + `market` |
| Market details | GET | 3 | `/markets/{epic}` | Rules, currencies, expiry, marketStatus |
| Prices | GET | 3 | `/prices/{epic}` | `resolution`, `max`, optional `from` |
| Open position | POST | 2 | `/positions/otc` | CreateOTCPositionV2 |
| Close position | DELETE† | 1 | `/positions/otc` | CloseOTCPositionV1 |
| Confirm | GET | 1 | `/confirms/{dealReference}` | Poll fill / reject reason |

† **Wire form:** IG FAQ — real HTTP DELETE drops the body →
`validation.null-not-allowed.request`. We send **POST** with header
`_method: DELETE` (same workaround as official `trading-ig`).

### Rate limiting (demo + live)

`IgRestClient` spaces **all** REST calls (~**3s** demo / ~**1.5s** live) so a
single session stays under IG’s api-key / account allowances. Allowance `403`s
raise `ExchangeRateLimitError` and are **not** retried (retrying burns quota).
Unit tests that inject a mock `http_client` skip pacing.

### Body gotchas

- **Open `currencyCode`:** from `instrument.currencies`, never account cash currency.
- **Open / close:** omit optional keys (`level`, `quoteId`, stops) instead of JSON `null`.
- **Close by dealId:** send `dealId` + opposite `direction` + `size` + `orderType=MARKET`.
  Do **not** also send `epic`/`expiry` — that is `validation.mutual-exclusive-value.request`.
  (Alternate close-by-epic path omits `dealId`; we use dealId.)

### Integration coverage (`tests/integration/test_ig_adapter_network.py`)

| Scenario | Gate |
|----------|------|
| Factory → demo host; session CST/token; account cash + currency | creds |
| Live factory still refused | creds |
| Instrument rules, marketStatus, OHLCV (1h/1d/5m), bad timeframe | creds |
| Portfolio seed from exchange | creds |
| cancel unsupported; LIMIT rejected; unknown confirm errors | creds |
| Long open → confirm → position → close → flat | `IG_DEMO_INTEGRATION=1` + TRADEABLE |
| Short open → confirm → position → close → flat | same |
| `run_demo_smoke` application path | same |

## Out of scope (still)

- Usable live IG orders
- Lightstreamer streaming
- Guaranteed stops / working orders / trailing stops
- Session calendars
- Full CFD margin model (cash affordability used as a conservative stand-in)
