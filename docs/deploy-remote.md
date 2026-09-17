# Remote deployment (VPS / always-on demo)

How to run the Docker stack on a remote machine and keep it updated.
For local Compose details (ports, Grafana profile, image layout) see
[`milestones/m9-docker.md`](milestones/m9-docker.md).

**Default sleeve:** Connors US500 IG demo (`TP_OVERLAY=ig-us500`, `ENV=demo`).

---

## What you need

| Item | Notes |
|------|--------|
| Small VPS | ~1 vCPU / 1–2 GB RAM is enough (Hetzner, DigitalOcean, …) |
| Ubuntu 22.04+ (or similar) | ARM (Pi) works with the same Compose file |
| SSH access | Key-based login recommended |
| Git remote | So the VPS can `git pull` updates |
| Secrets | Demo IG keys (+ optional Discord/Telegram) in a **server-local** `.env` |

Do **not** run the same IG demo account on your laptop and the VPS at the same
time — stop the local stack first: `docker compose down`.

---

## 1. First-time setup on the VPS

### Install Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"
# log out and back in (or reboot) so the docker group applies
docker version
```

### Clone the repo

```bash
git clone <your-repo-url> testingTheWaters
cd testingTheWaters
```

Use an SSH deploy key or HTTPS credential that can **read** the repo. Do not
put write credentials on the VPS unless you need them.

### Create `.env` on the server

```bash
cp .env.example .env
nano .env   # or vim / whatever
```

Minimum for the Connors US500 soak:

```bash
ENV=demo
TP_OVERLAY=ig-us500
IG_DEMO_API_KEY=...
IG_DEMO_USERNAME=...
IG_DEMO_PASSWORD=...
# optional: IG_DEMO_ACCOUNT_ID=...
# optional alerts: DISCORD_DEMO_WEBHOOK_URL=...  and/or TELEGRAM_*
```

`.env` is gitignored — it stays only on the machine. Never commit it or bake
keys into the image.

### Build and start

```bash
docker compose up -d --build
curl -sf http://localhost:8080/health
docker compose ps
docker compose logs -f trading-platform
```

Healthy look:

- `trading-platform` — status **healthy**, heartbeats for `IX.D.SPTRD.IFM.IP@1d`
- `trading-platform-prometheus` — **Up**
- `curl` → `{"status":"ok","uptime_seconds":…}`

### Optional: seed longer history

Demo auto-fetches instrument rules and can warm from IG OHLCV. For a fuller
Yahoo-style history in the volume:

```bash
docker compose run --rm trading-platform \
  trading-platform download-data --overlay ig-us500 --days 400
docker compose up -d
```

### Firewall

- Allow **SSH** (22 or your custom port).
- Prefer **not** exposing `8080` / `9091` publicly. Use Discord/Telegram for
  alerts, and SSH tunnels when you need the UIs:

```bash
# on your laptop
ssh -L 8080:localhost:8080 -L 9091:localhost:9091 user@YOUR_VPS_IP
# then: curl localhost:8080/health  and  open http://localhost:9091
```

If you do open ports, restrict them to your IP (ufw / cloud security group).

---

## 2. Day-to-day ops

| Task | Command (on the VPS, in the repo dir) |
|------|----------------------------------------|
| Follow logs | `docker compose logs -f trading-platform` |
| Health | `curl -sf http://localhost:8080/health` |
| Restart app only | `docker compose restart trading-platform` |
| Stop stack | `docker compose down` (keeps named volumes) |
| Start again | `docker compose up -d` |
| One-off smoke | Stop demo first, then see below |
| Wipe app data volume | `docker compose down -v` — **destructive** (state + cached bars) |

### Demo smoke on the VPS

```bash
docker compose stop trading-platform
docker compose run --rm trading-platform \
  trading-platform demo-smoke --overlay ig-us500
docker compose start trading-platform
```

---

## 3. Keeping the remote codebase updated

Workflow: develop / merge on your laptop → push to git → on the VPS pull and
rebuild.

### On your laptop (after changes are on the remote git branch)

```bash
git push origin main    # or your deploy branch
```

### On the VPS

```bash
cd ~/testingTheWaters   # or wherever you cloned

git status              # should be clean except ignored .env / local files
git pull --ff-only

docker compose up -d --build
curl -sf http://localhost:8080/health
docker compose logs --tail 50 trading-platform
```

`--build` rebuilds the image when `Dockerfile`, `src/`, `config/`, or the lock
file changed. Compose recreates the container; the **`tp-data` volume is kept**,
so demo state and cached bars survive updates.

### If `git pull` complains about local changes

The VPS should not carry local edits. Typical cases:

```bash
# You only care about restoring a clean tree (keeps .env — it is gitignored)
git fetch origin
git reset --hard origin/main
docker compose up -d --build
```

Do **not** `reset --hard` if you deliberately keep uncommitted server-only
patches (prefer putting those in git or a compose override file instead).

### `.env` changes (secrets / overlay)

`.env` is not updated by `git pull`. Edit it on the server, then recreate:

```bash
nano .env
docker compose up -d        # recreates containers that depend on env
```

No image rebuild needed unless you also changed code.

### Config YAML changes

Overlays under `config/` are **copied into the image** at build time. After
`git pull`, always `docker compose up -d --build` so the new YAML is in the
image.

### Checking what version is running

```bash
git rev-parse --short HEAD
docker compose images
docker compose exec trading-platform trading-platform version
```

---

## 4. Raspberry Pi notes

Same clone → `.env` → `docker compose up -d --build` flow. Prefer a USB SSD
for Docker’s data root if the soak will run for months (SD cards wear out).

---

## 5. Rollback

```bash
git log --oneline -5
git checkout <known-good-sha>
docker compose up -d --build
```

To return to latest:

```bash
git checkout main
git pull --ff-only
docker compose up -d --build
```

Volumes are unchanged by rollback unless you pass `-v`.

---

## 6. Troubleshooting

| Symptom | What to check |
|---------|----------------|
| `dependency trading-platform failed to start` | `docker compose logs trading-platform` — often missing deps (rebuild), bad `.env`, or IG auth |
| Unhealthy forever | IG login slow / rate limit — wait through `start_period`; confirm `ENV=demo` + `IG_DEMO_*` |
| No signals for days | Expected for daily Connors — sparse; check heartbeats still advance `last_bar` |
| Two machines fighting | Only one host should run demo against that IG account |
| After pull, old behaviour | Forgot `--build` — config/code still from old image layers |

---

## Related

- Local Compose / Prometheus / Grafana: [`milestones/m9-docker.md`](milestones/m9-docker.md)
- IG demo credentials & smoke: [`milestones/m8c-ig-demo-adapter.md`](milestones/m8c-ig-demo-adapter.md)
- Git branching: [`git-workflow.md`](git-workflow.md)
