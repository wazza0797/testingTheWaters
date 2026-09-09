# Milestone 8a — Demo Execution (Exchange Sandbox)

**Status:** Complete (Phases A–C)

**Depends on:** Milestone 6 (paper loop / portfolio), Milestone 7 (notifications)

**Unblocks:** Further venue adapters; Milestone 8b (mainnet live) after demo soak

## Goals

Place **real API orders** against an exchange’s **demo / practice / testnet**
account (fake balances, venue-side fees & matching), using the **same**
strategy → risk → execution → portfolio → notification path as local paper
and future live — only the broker + adapter config change.

Local `paper` (`PaperBroker` + `FillSimulator`) stays for fast offline sims.
Demo is for “does this venue’s order API and account behaviour feel right?”

## Design principles (same as the rest of the platform)

1. **Exchange-agnostic application code.** Strategies, risk, `ExecutionHandler`,
   portfolio, and notifications never import Binance / Trading 212 / ccxt.
   All venue quirks live under `exchanges/<name>/`.
2. **Mode ≠ exchange.** `ENV` / execution mode (`paper` | `demo` | `live` |
   `backtest`) is orthogonal to `trading.exchange` (`binance`, later
   `trading212`, …).
3. **One broker port.** `IBroker` implementations:
   - `SimBroker` — backtest
   - `PaperBroker` — local simulated fills
   - `DemoBroker` — orders via `IExchangeAdapter` (any sandbox venue)
   - `LiveBroker` — same port, mainnet + double gate (later, not this milestone)
4. **Adapter owns sandbox wiring.** Base URLs, demo API keys, and fill-status
   polling/WS mapping are adapter concerns. `DemoBroker` only calls the port.
5. **Fills come from the venue.** Demo does **not** use `FillSimulator`.
   Exchange reports are mapped into domain `Fill` models.
6. **Composition root selects the stack.** `container.py` picks adapter +
   broker from `(exchange, mode)` — adding Trading 212 is a new adapter
   package + factory branch, not a fork of the demo loop.

## Out of scope (this milestone)

- Mainnet live orders (`ENV=live` + `LIVE_TRADING_ENABLED`) — Milestone 8b
- Full user-data WebSocket (polling order status is enough for v1)
- Replacing local paper mode
- Additional venues beyond Binance + IG (same adapter + factory seam)

## Deliverables

| Piece | Role |
|-------|------|
| `Environment.DEMO` | Distinct from paper and live |
| `ExchangeOrderStatus` (+ port methods) | Venue-neutral order/fill polling |
| `DemoBroker` | `IBroker` over `IExchangeAdapter` |
| `exchanges/factory.py` | `build_exchange_adapter(exchange, mode, settings)` |
| Binance demo URL/keys wiring | First concrete adapter behind the factory |
| IG Markets demo adapter | Second venue — [`m8c-ig-demo-adapter.md`](m8c-ig-demo-adapter.md) |
| `config/demo.yaml` + `DemoConfig` | Overlay for `trading-platform demo` |
| CLI `demo` + `demo-smoke` | Full loop + min-size open/close pipeclean |
| Docs / roadmap | Architecture diagram includes `DemoBroker` |

## Phased implementation

### Phase A+B — Scaffold + Binance Demo ✅

- Milestone doc, `ENV=demo`, domain order-status model, port extensions
- `DemoBroker` with fakes in unit tests (no network)
- Exchange factory seam + `BINANCE_DEMO_API_KEY` / `BINANCE_DEMO_API_SECRET`
- `BinanceAdapter.for_demo` via ccxt `enable_demo_trading`
- `place_order` / `cancel_order` / `get_balance` / `fetch_order` on Binance adapter
- Portfolio seeded from exchange free balances (no local `starting_cash`)
- `config/demo.yaml`, CLI `trading-platform demo`, `DemoTradingLoop`
- Roadmap / architecture updates

### Phase C — Second venue (IG) ✅

- [`m8c-ig-demo-adapter.md`](m8c-ig-demo-adapter.md): `exchanges/ig/`, demo
  working, live scaffolded + factory-gated
- Platform shorts via `InstrumentRules.allows_short` (spot false, IG CFD true)
- Rate-limited IG REST client; gated live network integration tests
- Discord webhooks selected by `ENV` (paper / demo / live)

## Acceptance criteria

- Application modules depend only on `IExchangeAdapter` / `IBroker` for demo
- Adding an exchange is adapter + factory + config name — not a new demo loop
- `ENV=demo` cannot place mainnet orders (adapter constructed with demo URLs only)
- Unit tests cover `DemoBroker` with a fake adapter (no network)
- Binance Phase B: demo fill triggers same notification path as paper fills
- IG Phase C: demo open/close path works; live factory still refused
