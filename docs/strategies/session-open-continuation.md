# Session-Open Continuation (US500)

Research spec for a genuine-auction open-continuation strategy. This document
locks the design so the research/validate scripts have one authoritative
reference — do not add parameters, markets, or exit logic beyond what is
written here without updating this doc first.

## Hypothesis

At a market with a genuine single-price opening auction (equity index cash
open), early informed order flow tends to continue pushing price in the same
direction for a bounded period after the open, rather than immediately mean
revert. This is distinct from:

- Connors RSI(2) reversal (`connors-core-tilt-runner.md`) — trades *against*
  an already-established short-term move.
- Session breakout (`research_ig_session_breakout.py`) — trades range breaks
  during a session window, not the open itself.

FX is explicitly **out of scope**: FX has no single opening auction (it is a
continuous 24h interbank/CFD book), so the "informed order flow clears at the
open" mechanism this strategy tests does not apply. Do not add FX back into
Phase A/B.

## Locked scope

- **Instrument**: US500 cash, epic `IX.D.SPTRD.IFM.IP` (Yahoo `^GSPC` stand-in
  for research — see `scripts/backfill_ig_external.py` docstring caveats).
- **Session**: 09:30 `America/New_York` cash open. Timezone-aware (DST
  handled by `zoneinfo`, not a fixed UTC offset — contrast with
  `scripts/research_ig_session_breakout.py`'s `_SESSION` dict, which ignores
  DST).
- **Phase B** (only if Phase A clears cleanly): DAX Xetra open, identical
  rule, no retune.
- **Out of scope for A/B**: FX, commodities, exit engineering, parameter
  grids.

## Design (no grid — every constant below is fixed in script, not searched)

- **Measurement windows**: 30 minutes and 60 minutes. No other windows.
- **Signal**: `signal_return = close(window_end) - open(09:30 ET)`.
  `direction = sign(signal_return)`.
- **Minimum-move filter**: skip the session if
  `abs(signal_return) < 0.5 * trailing_20_session_avg(abs(signal_return))`
  for that same measurement length (chop filter — don't trade a directionless
  open). The trailing average only uses *prior* sessions (no lookahead); the
  first 20 sessions of any series are unavoidably skipped (warm-up).
- **Entry**: at the measurement-window close (information-time entry — the
  window-close price is known and tradeable at that instant, unlike a
  next-bar-open convention used elsewhere in this repo for daily signals).
- **Exit**: fixed same-day 16:00 `America/New_York` (RTH cash close). No
  trailing stop, no indicator exit, no time-of-day variation. Every session
  is flat by the close — no overnight/financing exposure.
- **Sizing**: `qty = (equity * RISK_PCT) / trailing_20_session_avg(window_range)`,
  where `window_range = high(window) - low(window)` for that session's
  measurement window, and `RISK_PCT` is a fixed constant in the research
  script (`0.01`). This is an ATR-style volatility proxy *scoped to the
  measurement window itself* (not a standard daily `ATR(14)`) — the same
  20-session lookback as the min-move filter, for one documented, consistent
  window instead of two independent constants.
- **Cost**: fixed IG US500 RTH-open dealing spread (locked below), **not**
  `config/ig-us500.yaml`'s `spread_bps: 1.0` daily-average figure. No
  financing/overnight cost is modelled — every position is closed by 16:00 ET
  the same day it was opened.

## Locked cost: IG US500 RTH-open spread

Source: IG Help Centre, "CFD Indices product details" (US 500 row), checked
2026-09-25:
<https://www.ig.com/en/help-and-support/articles/691241-what-are-ig-s-indices-cfd-product-details>

| Session (EST)     | Dealing spread (points) |
|--------------------|:-----------------------:|
| 09:30–16:00 (RTH)  | **0.4**                 |
| 17:00–18:00        | 1.5                     |
| All other times    | 0.6                     |

Both the measurement-window entry (09:30–10:30 ET at the latest) and the
16:00 ET exit fall inside the 09:30–16:00 RTH bucket, so a single flat
constant applies to both fills: `IG_US500_OPEN_SPREAD_POINTS = 0.4`. Value
per point is $1 (standard US500 cash CFD), so the dollar cost of one fill at
quantity `qty` is `qty * 0.4`. Consistent with the cost convention already
used in `scripts/research_connors_rsi2.py` (full spread charged at *each*
fill, entry and exit, rather than a half-spread per side), the round-trip
drag is conservatively `qty * 0.8` points-equivalent. Re-check this table
before any live/demo promotion — IG spreads are published as indicative and
can change.

## Reject rule

If the full-history read or the stitched rolling out-of-sample (OOS) read
fails or is inconsistent (not consistently positive), **stop**. Do not widen
measurement windows, add markets, or add exit logic to rescue a failing
result. A clean negative is an accepted, useful outcome — it means the open
does not carry a tradeable continuation edge on this instrument at this
cost, and the mechanism should not be pursued further here.

## Phase 0 data findings (2026-09-25)

- **On disk before Phase 0**: `IX.D.SPTRD.IFM.IP` 1h bars, 2023-10-13 to
  2026-09-11 (5,083 bars, ~726 RTH sessions, first bar of each session is
  exactly 09:30–10:30 ET — i.e. already a 60-minute window with no
  aggregation needed).
- **Phase 0 backfill**: `^GSPC` 15m via `scripts/backfill_ig_external.py`.
  Yahoo returned **1,556 bars, 2026-07-02 to 2026-09-25 (~60 RTH sessions,
  ~85 calendar days)** — the documented ~60-trading-day intraday cap, not a
  fixed 60 calendar days. 26 bars/session (09:30–16:00 ET in 15-minute
  increments); window-end bar for 30m = the 2nd bar (09:45–10:00 ET), for
  60m = the 4th bar (10:15–10:30 ET).
- **Depth verdict**: 15m history (~60 sessions) is too short to power a
  meaningful rolling walk-forward OOS test on its own — a handful of thin,
  overlapping folds only. Per the plan's fallback: report 15m full-history +
  a thin/indicative rolling OOS for **both** windows (30m and 60m), and
  *additionally* report 60m-on-1h (the ~726-session, ~3-year series) as the
  decision-grade depth check for the 60-minute window. The 30-minute window
  has no deeper series available (1h bars cannot reconstruct a 30-minute
  intraday window) — its verdict rests on the thin 15m read alone and should
  be treated as indicative regardless of outcome.

## Deliverables

1. This document.
2. `scripts/research_session_open_continuation.py` — full-history +
   simple IS/OOS split screen for 15m/meas=30, 15m/meas=60, 1h/meas=60(depth
   check). Writes `data/research/session_open_continuation.csv`.
3. `scripts/validate_session_open_continuation.py` — full-history + rolling
   walk-forward OOS (fixed params, no grid search — IS is a spacer only),
   using `iter_walk_forward_windows`/`stitch_oos_equity`, windows sized by
   session count per dataset. Prints an explicit promote/reject verdict per
   candidate and an overall Phase A verdict.
4. No live strategy class, overlay YAML, or demo/paper wiring until Phase A
   clears with a promote verdict.

## Done when

Phase A completes with either a promote or a clean-negative (reject)
verdict, following the reject rule above with no parameter/market/exit
rescue attempts. Phase B (DAX Xetra open) is not started unless Phase A
clears.

## Phase A result (2026-09-25) — REJECT, clean negative

Ran `scripts/validate_session_open_continuation.py` (full history + rolling
walk-forward OOS, session-sized windows, fixed params). Full run log:
`data/research/session_open_continuation_phase_a.log`; screen log:
`data/research/session_open_continuation_screen.log`; CSV:
`data/research/session_open_continuation.csv`.

| Candidate                | Grade       | Full-history return | OOS folds pos. | Stitched OOS | Verdict |
|---------------------------|-------------|---------------------:|:---------------:|-------------:|---------|
| 1h/meas60 (~730 sessions)  | decision    | -3.59% (vs B&H +79.39%) | 4/8 | +2.41% | **REJECT** |
| 15m/meas30 (~60 sessions)  | indicative  | +2.73%               | 3/4              | +4.34%        | PROMOTE (small-sample) |
| 15m/meas60 (~60 sessions)  | indicative  | +0.35%               | 3/4              | +2.73%        | PROMOTE (small-sample) |

**Overall Phase A verdict: REJECT.** The decision-grade 1h/meas60 read (the
only candidate with real depth — ~3 years, ~730 sessions) is negative on
full history and only 50% of OOS folds positive (below the 60% consistency
bar), while buy-and-hold returned +79% over the same window — the mechanism
is not adding value; it is roughly a coin-flip with cost drag on top of a
strong secular uptrend. The two 15m candidates "promoted" on a ~60-session
(~3 month), single-regime (rising market) sample — far too small and too
narrow a regime window to be trusted over the decision-grade result, and
consistent with what you'd expect from noise in a short, trending sample.

Per the reject rule, this is where the investigation stops: no widened
measurement windows, no added markets, no exit-logic changes to try to
rescue the thin-sample "promote" reads. **Phase B (DAX Xetra open) is not
started** — Phase A did not clear. If a session-open continuation mechanism
is revisited later, it should start from a fresh hypothesis or a materially
longer 15m/30m dataset (a different intraday data source, since Yahoo caps
at ~60 trading days), not from tuning this locked design.
