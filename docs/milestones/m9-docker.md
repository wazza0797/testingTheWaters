# Milestone 9 — Docker / VPS deployment

**Status:** Complete

**Depends on:** M0 observability (`/health`, `/metrics`), M8a demo loop

## Goals

- Reproducible container image for always-on **demo** (and paper) soaks
- Persist `DATA_DIR` across restarts (parquet warmup + demo/paper state)
- Publish `/health` for orchestration healthchecks
- Optional **Prometheus** sidecar scraping `/metrics`
- Optional **Grafana** (compose profile) with Prometheus datasource and a
  provisioned **Trading Platform — Observability** dashboard covering the full
  metric catalog (process, throughput, rejects, handler latency)
- Document VPS / Raspberry Pi runbooks — secrets never baked into the image

## Delivered

- `Dockerfile` — Python 3.12 + `uv sync --frozen`, non-root user, `DATA_DIR=/data`
- `.dockerignore` — keeps secrets and local `data/` out of the build context
- `docker-compose.yml`:
  - `trading-platform` — default `demo --overlay ${TP_OVERLAY:-ig-us500}`
  - `prometheus` — scrapes `trading-platform:9090/metrics`; UI on host `:9091`
  - `grafana` — profile `grafana`, datasource auto-provisioned
- `docker/prometheus/prometheus.yml`, `docker/grafana/provisioning/…`
- Paper + demo CLI start an observability **sidecar** (system monitor +
  `/health` on `HEALTH_PORT` + `/metrics` on `METRICS_PORT`) when
  `OBSERVABILITY_ENABLED=true`. Health and metrics are **separate** apps so
  publishing `:8080` does not expose `/metrics`. Sidecar does not emit
  `Heartbeat` (paper/demo loops already do).
- Paper + demo **auto-fetch** instrument rules on cache miss; strategy warmup
  persists venue OHLCV into parquet so redeploys keep SMA/ATR history
- `.env.example` — `TP_OVERLAY` + optional host port / Grafana knobs

## How it works

```
┌─────────────────────────────────────────────┐
│  Docker host (VPS or Pi)                    │
│  ┌──────────────────┐   scrape   ┌────────┐ │
│  │ trading-platform │◄───────────│ Prom   │ │
│  │ demo loop        │  :9090     │ :9090  │ │
│  │ /health :8080    │            └────────┘ │
│  │ volume: /data    │                       │
│  └──────────────────┘                       │
└─────────────────────────────────────────────┘
         │                         │
    host :8080                host :9091
```

- **Image** packages code + deps only.
- **`.env`** supplies `ENV=demo`, `IG_DEMO_*` (or Binance demo keys), overlays.
- **Volume `tp-data`** → container `/data` so state survives `compose down` /
  rebuilds (unless you `docker volume rm`).

## Runbook

```bash
# 1. Secrets (gitignored)
cp .env.example .env
# Set ENV=demo and IG_DEMO_* (or BINANCE_DEMO_*). TP_OVERLAY=ig-us500 by default.

# 2. Build + start app + Prometheus
docker compose up -d --build

# 3. Check
curl -sf http://localhost:8080/health
docker compose logs -f trading-platform
open http://localhost:9091   # Prometheus → Status → Targets

# 4. Optional Grafana (provisioned observability dashboard)
docker compose --profile grafana up -d
open http://localhost:3000   # admin / admin (change via GRAFANA_ADMIN_*)
# Dashboards → Trading Platform → Trading Platform — Observability
```

### Seed history (recommended for Connors SMA200)

Either bind-mount a pre-warmed host `data/` over `/data`, or exec once:

```bash
docker compose run --rm trading-platform \
  trading-platform download-data --overlay ig-us500 --days 400
docker compose up -d
```

### Override sleeve / command

```bash
TP_OVERLAY=demo docker compose up -d
# or
docker compose run --rm trading-platform trading-platform demo-smoke --overlay ig-us500
```

### Raspberry Pi

Same compose file. On ARM64:

```bash
docker compose build
docker compose up -d
```

Prefer USB SSD over SD card for the Docker data root / named volumes.

### VPS / remote always-on

Full first-time setup, firewall, and **how to pull updates on the server**:
[`docs/deploy-remote.md`](../deploy-remote.md).

## Acceptance criteria

| Criterion | Status |
|-----------|--------|
| `docker compose up -d --build` starts trading-platform + Prometheus | ✅ |
| `GET /health` returns ok while demo is running | ✅ (sidecar) |
| Prometheus target `trading-platform:9090` is UP | ✅ |
| `DATA_DIR` / named volume persists demo state across restart | ✅ |
| Secrets via `.env`, not image layers | ✅ |
| Grafana optional via `--profile grafana` | ✅ |
| Milestone doc + README deploy section | ✅ |

## Out of scope (still)

- Publishing images to a registry / CD pipeline
- Live (`ENV=live`) compose profile (M8b)
- Hardened multi-org Grafana beyond the provisioned overview dashboard
- Kubernetes / Nomad
