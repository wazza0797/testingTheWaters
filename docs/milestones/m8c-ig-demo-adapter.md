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
3. In `config/demo.yaml`, set `trading.exchange: ig` and `trading.symbol` to an
   **epic** (e.g. `CS.D.EURUSD.MINI.IP`), not `BASE/QUOTE`.
4. `uv run trading-platform demo`

## Delivered

- `exchanges/ig/` — client, mapper, `IgAdapter.for_demo` / `for_live`
- Factory branch `ig`; settings + `.env.example`
- CFD portfolio seed via `get_balance("ACCOUNT")` + epic qty
- Platform shorts gated by `InstrumentRules.allows_short` (Binance spot False)

## Out of scope (still)

- Usable live IG orders
- Lightstreamer streaming
- Guaranteed stops / working orders / trailing stops
- Session calendars
- Full CFD margin model (cash affordability used as a conservative stand-in)
