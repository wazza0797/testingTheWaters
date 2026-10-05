# Connors RSI(2) Core+Tilt Runner

**Venue:** IG Markets (cash CFDs)
**Markets:** US500, Nasdaq, DAX
**Overlays:** `ig-connors-tilt-us500`, `ig-connors-tilt-nasdaq`, `ig-connors-tilt-dax`

## Locked research inputs (do not re-derive)

| Piece | Spec |
|-------|------|
| Signal | Connors RSI(2) < 15 → tilt entry |
| Regime | Close > SMA200 → tilt eligible; below → regime exit |
| Fills | Next-open only (`use_next_bar_open` / `latency_bars=1`) |
| Tilt gate | `rv(10) / mean(rv10, 60) ≥ 1.5` → **skip new entries** (open tilts untouched) |
| Mid-hold exit | **No** SMA200∧vol or vol-only mid-hold exit. Tested (DAX Volmageddon + OOS gap trades): bare SMA200 exit wins. Absence is intentional. |
| US sizing | Shared **1.5%** bucket, Sharpe-weighted **39% US500 / 61% Nasdaq** |
| DAX sizing | Separate **~1%** leg; **no** three-way simultaneous scaler |
| DAX caveat | 1.5× throttle is precautionary (n=1 covid fill) — log skips, don't silently harden |

Scale the US bucket to 2.0% only after a demo/paper soak confirms live tracking — not day-one.

## Position architecture

Each market tracks two legs internally (`LegBook`):

- `core` — static long, seeded once (demo: existing venue size attributed as core)
- `tilt` — 0 or 1 add-on, opened/closed by this strategy

Net CFD size on IG = core + tilt. Orders are tagged `leg` + `reason` (`entry` | `reversion_exit` | `regime_exit` | `time_stop`).

## Daily loop (implemented in `ConnorsCoreTiltStrategy`)

1. Indicators: RSI(2), SMA200, SMA5, ATR, vol ratio
2. If tilt open and close < SMA200 → queue **regime_exit** (HIGH Discord)
3. Else if tilt open and close > SMA5 → **reversion_exit**
4. Else if tilt open and bars ≥ 10 → **time_stop**
5. Else if flat, bull regime, fresh RSI < 15:
   - vol ≥ 1.5 → log skip (low priority)
   - else → queue **entry** at ATR risk with locked `risk_pct`

## Risk engine

`PassThroughRiskEngine` allows tilt **add** while long (`leg=tilt`, `reason=entry`) and tilt **partial close** (`leg=tilt`, `close_qty=…`) so core is preserved.

## Monitoring

| Event | Level |
|-------|-------|
| Tilt entry / reversion exit fill | info |
| Vol throttle skip | info (log only) |
| Regime exit fill | warning (HIGH) |
| Fill / risk reject | existing notification path |

## Pre-live checklist

1. Demo soak on all three overlays
2. Confirm IG historical daily closes align with research assumptions
3. Dry-run a joint US500+Nasdaq signal day — combined tilt risk should stay ≤ 1.5%
4. Core seed / quarterly resize is out of the daily loop

## Constants

See `trading_platform.risk.connors_core_tilt`.
