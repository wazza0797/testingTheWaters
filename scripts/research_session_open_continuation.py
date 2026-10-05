#!/usr/bin/env python3
"""Session-open continuation research — US500 cash open, no grid.

Hypothesis: at a genuine single-open auction (US500 cash, 09:30
America/New_York, DST-aware), the direction of the first N minutes' move
tends to *continue* into the same session's close rather than mean-revert —
informed order flow at the open, not an already-established-move signal
(that is the separate Connors RSI(2) program). FX is out of scope: it has no
single open, just a continuous 24h book.

Full locked design: docs/strategies/session-open-continuation.md. Summary:
  - Instrument: US500 cash, IX.D.SPTRD.IFM.IP (Yahoo ^GSPC stand-in).
  - Measurement windows: 30m and 60m only. No grid.
  - Signal: return from the 09:30 ET open to the window close; trade that
    sign. Filter: skip if abs(signal_return) < 0.5 * trailing 20-session
    average abs(signal_return) for that same window length.
  - Entry: at the measurement-window close (information-time, not next-bar).
  - Exit: fixed same-day 16:00 ET close. No trailing/indicator exit.
  - Size: qty = (equity * RISK_PCT) / trailing-20-session average intra-
    window range (an ATR-style proxy scoped to the measurement window).
  - Cost: fixed IG US500 RTH-open dealing spread (0.4 pts, locked in the
    spec doc), charged at both entry and exit (same convention as
    research_connors_rsi2.py). No financing — flat by 16:00 ET every day.

Data:
  - 15m bars (primary, both windows): Yahoo caps intraday history at ~60
    trading days — thin (~60 sessions). Reported full-history + a thin/
    indicative rolling IS/OOS split here.
  - 1h bars (depth check, 60m window only): ~3 years on disk. First bar of
    each session is exactly the 09:30-10:30 ET window already.

Usage:
    uv run python scripts/backfill_ig_external.py --yahoo '^GSPC' \\
        --epic IX.D.SPTRD.IFM.IP --timeframe 15m
    uv run python scripts/research_session_open_continuation.py
"""

from __future__ import annotations

import argparse
import csv
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from trading_platform.config.settings import Settings
from trading_platform.domain.models.bar import Bar
from trading_platform.market_data.repository.parquet import ParquetMarketDataRepository
from trading_platform.notifications.research import notify_demo_research

_EXCHANGE = "ig"
_EPIC = "IX.D.SPTRD.IFM.IP"
_SESSION_TZ = ZoneInfo("America/New_York")
_SESSION_OPEN = time(9, 30)
_SESSION_CLOSE = time(16, 0)

# Locked design constants — see docs/strategies/session-open-continuation.md.
# Not searched; do not turn these into a grid (reject-rule discipline).
MEASUREMENT_MINUTES: tuple[int, ...] = (30, 60)
LOOKBACK_SESSIONS = 20
MIN_MOVE_FILTER_MULT = 0.5
RISK_PCT = 0.01
STARTING_CASH = 50_000.0

# IG US 500 cash CFD dealing spread, 09:30-16:00 EST bucket (covers both the
# measurement-window entry and the 16:00 exit). Source: IG Help Centre "CFD
# Indices product details" (US 500 row), checked 2026-09-25:
# https://www.ig.com/en/help-and-support/articles/691241
# Value per point = $1 (standard US500 cash CFD). Charged at both entry and
# exit (same convention as research_connors_rsi2.py) -> round-trip drag of
# qty * 2 * IG_US500_OPEN_SPREAD_POINTS.
IG_US500_OPEN_SPREAD_POINTS = 0.4

Dataset = Literal["15m", "1h"]


@dataclass(frozen=True, slots=True)
class SessionSample:
    session_date: date
    open_price: float
    window_close_price: float
    window_high: float
    window_low: float
    exit_time: datetime
    exit_price: float


@dataclass(frozen=True, slots=True)
class TradeRecord:
    session_date: date
    traded: bool
    direction: int
    signal_return: float
    pnl_pct: float
    equity_after: float
    exit_time: datetime


@dataclass(frozen=True, slots=True)
class SimResult:
    n_sessions: int
    n_trades: int
    return_pct: float
    maxdd_pct: float
    sharpe: float | None
    win_rate: float | None
    avg_trade_pct: float | None
    bh_pct: float
    vs_bh: float


def build_sessions(bars: list[Bar], *, meas_minutes: int) -> list[SessionSample]:
    """Group bars into RTH sessions (America/New_York) and slice each into
    its measurement window (from 09:30 ET) + same-day 16:00 ET exit.

    A bar belongs to the measurement window if its *local start time* is
    strictly before `09:30 + meas_minutes` — e.g. for 15m bars and a 30m
    window this is the first 2 bars (09:30, 09:45); for 1h bars and a 60m
    window this is exactly the first bar (09:30-10:30).
    """
    by_day: dict[date, list[Bar]] = {}
    for bar in bars:
        local = bar.timestamp.astimezone(_SESSION_TZ)
        by_day.setdefault(local.date(), []).append(bar)

    sessions: list[SessionSample] = []
    for day in sorted(by_day):
        day_bars = sorted(by_day[day], key=lambda b: b.timestamp)
        first_local = day_bars[0].timestamp.astimezone(_SESSION_TZ)
        if first_local.time() != _SESSION_OPEN:
            # Half day / irregular open (early holiday close, data gap) —
            # skip rather than guess at a different open time.
            continue
        window_cutoff = datetime.combine(day, _SESSION_OPEN, tzinfo=_SESSION_TZ) + timedelta(
            minutes=meas_minutes
        )
        window_bars = [b for b in day_bars if b.timestamp.astimezone(_SESSION_TZ) < window_cutoff]
        if not window_bars:
            continue
        expected_window_bars = max(1, meas_minutes // 15) if meas_minutes < 60 else None
        # For 15m data, require the window to actually be fully covered
        # (skip an early-close/partial session rather than measure a short
        # window as if it were the full one).
        first_gap = day_bars[1].timestamp - day_bars[0].timestamp if len(day_bars) > 1 else None
        if (
            first_gap
            and first_gap <= timedelta(minutes=30)
            and expected_window_bars
            and len(window_bars) < expected_window_bars
        ):
            continue
        sessions.append(
            SessionSample(
                session_date=day,
                open_price=float(day_bars[0].open),
                window_close_price=float(window_bars[-1].close),
                window_high=max(float(b.high) for b in window_bars),
                window_low=min(float(b.low) for b in window_bars),
                exit_time=day_bars[-1].timestamp,
                exit_price=float(day_bars[-1].close),
            )
        )
    return sessions


def _max_drawdown(equity: list[float]) -> float:
    peak = equity[0]
    max_dd = 0.0
    for x in equity:
        peak = max(peak, x)
        dd = (x - peak) / peak if peak else 0.0
        max_dd = min(max_dd, dd)
    return max_dd * 100.0


def _sharpe(session_rets: list[float]) -> float | None:
    if len(session_rets) < 30:
        return None
    n = len(session_rets)
    mean = sum(session_rets) / n
    var = sum((r - mean) ** 2 for r in session_rets) / (n - 1)
    sd = math.sqrt(var)
    if sd <= 1e-18:
        return None
    return mean / sd * math.sqrt(252.0)


def simulate_sessions(
    sessions: list[SessionSample],
    *,
    spread_points: float,
    risk_pct: float = RISK_PCT,
    lookback: int = LOOKBACK_SESSIONS,
    min_move_mult: float = MIN_MOVE_FILTER_MULT,
    starting_cash: float = STARTING_CASH,
) -> list[TradeRecord]:
    """Chronological, no-lookahead simulation. Every trailing stat at session
    `i` only uses sessions `[i-lookback, i)` — never the current session.
    """
    abs_return_hist: list[float] = []
    range_hist: list[float] = []
    equity = starting_cash
    records: list[TradeRecord] = []

    for s in sessions:
        signal_return = s.window_close_price - s.open_price
        window_range = s.window_high - s.window_low
        traded = False
        direction = 0
        pnl_dollars = 0.0
        equity_before = equity

        if len(abs_return_hist) >= lookback:
            avg_abs_ret = sum(abs_return_hist[-lookback:]) / lookback
            avg_range = sum(range_hist[-lookback:]) / lookback
            if abs(signal_return) >= min_move_mult * avg_abs_ret and avg_range > 0:
                direction = 1 if signal_return > 0 else -1
                qty = (equity * risk_pct) / avg_range
                entry_price = s.window_close_price
                exit_price = s.exit_price
                gross_pnl = direction * qty * (exit_price - entry_price)
                cost = qty * spread_points * 2  # full spread charged at each fill
                pnl_dollars = gross_pnl - cost
                traded = True

        equity = equity_before + pnl_dollars
        abs_return_hist.append(abs(signal_return))
        range_hist.append(window_range)
        records.append(
            TradeRecord(
                session_date=s.session_date,
                traded=traded,
                direction=direction,
                signal_return=signal_return,
                pnl_pct=(pnl_dollars / equity_before * 100.0) if equity_before else 0.0,
                equity_after=equity,
                exit_time=s.exit_time,
            )
        )
    return records


def summarize(records: list[TradeRecord], sessions: list[SessionSample]) -> SimResult:
    """Summarize a (possibly sliced) run of `records` as its own local series.

    Rebuilds the equity curve by compounding each record's `pnl_pct` fresh
    from `STARTING_CASH`, rather than reading `equity_after` directly — this
    makes the summary correct both for a genuinely fresh `simulate_sessions`
    call *and* for a slice taken out of one long chronological run (e.g. one
    walk-forward OOS fold), where `equity_after` reflects prior compounding
    from earlier in the series and would give a nonsensical local return.
    """
    equity_curve = [STARTING_CASH]
    for r in records:
        equity_curve.append(equity_curve[-1] * (1.0 + r.pnl_pct / 100.0))
    trades = [r for r in records if r.traded]
    session_rets_pct = [r.pnl_pct for r in records]
    wins = sum(1 for r in trades if r.pnl_pct > 0)
    return_pct = (equity_curve[-1] / equity_curve[0] - 1.0) * 100.0 if equity_curve[0] else 0.0
    bh_pct = 0.0
    if sessions:
        bh_pct = (sessions[-1].exit_price / sessions[0].open_price - 1.0) * 100.0
    return SimResult(
        n_sessions=len(records),
        n_trades=len(trades),
        return_pct=return_pct,
        maxdd_pct=_max_drawdown(equity_curve),
        sharpe=_sharpe(session_rets_pct),
        win_rate=(wins / len(trades)) if trades else None,
        avg_trade_pct=(sum(r.pnl_pct for r in trades) / len(trades)) if trades else None,
        bh_pct=bh_pct,
        vs_bh=return_pct - bh_pct,
    )


def _print_result(label: str, res: SimResult) -> None:
    sharpe = f"{res.sharpe:.2f}" if res.sharpe is not None else "n/a"
    win = f"{res.win_rate * 100:.0f}%" if res.win_rate is not None else "n/a"
    avg = f"{res.avg_trade_pct:+.3f}%" if res.avg_trade_pct is not None else "n/a"
    print(
        f"  {label:<10} n={res.n_sessions:>4} trades={res.n_trades:>4} "
        f"ret={res.return_pct:+7.2f}% vs_bh={res.vs_bh:+7.2f}% "
        f"maxdd={res.maxdd_pct:6.2f}% sharpe={sharpe:>5} win={win:>4} avg={avg}"
    )


def _split(
    sessions: list[SessionSample], *, is_frac: float
) -> tuple[list[SessionSample], list[SessionSample]]:
    cut = int(len(sessions) * is_frac)
    return sessions[:cut], sessions[cut:]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--is-frac", type=float, default=0.6, help="IS fraction for quick split.")
    parser.add_argument(
        "--csv", type=Path, default=Path("data/research/session_open_continuation.csv")
    )
    parser.add_argument("--no-discord", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    repo = ParquetMarketDataRepository(Path(settings.data_dir), exchange=_EXCHANGE)

    print("Session-open continuation — US500 cash open (09:30 America/New_York)")
    print(
        f"spread={IG_US500_OPEN_SPREAD_POINTS}pts (09:30-16:00 EST, charged both fills) "
        f"risk_pct={RISK_PCT:.1%} lookback={LOOKBACK_SESSIONS} "
        f"min_move_mult={MIN_MOVE_FILTER_MULT} financing=none (flat by 16:00 ET)\n"
    )

    rows: list[dict[str, object]] = []
    summary_lines: list[str] = []

    candidates: list[tuple[Dataset, int, str]] = [
        ("15m", 30, "15m/meas30"),
        ("15m", 60, "15m/meas60"),
        ("1h", 60, "1h/meas60 (depth check)"),
    ]
    for dataset, meas, label in candidates:
        bars = list(repo.load_bars(_EPIC, dataset))
        if not bars:
            print(f"# {label}: no {dataset} bars — run the backfill first.")
            continue
        sessions = build_sessions(bars, meas_minutes=meas)
        span_days = (bars[-1].timestamp - bars[0].timestamp) / timedelta(days=1)
        print(
            f"# {label}: {dataset} bars={len(bars)} sessions={len(sessions)} "
            f"({bars[0].timestamp.date()}->{bars[-1].timestamp.date()}, ~{span_days:.0f}d)"
        )
        if len(sessions) <= LOOKBACK_SESSIONS + 5:
            print(f"  SKIP: only {len(sessions)} sessions, need > {LOOKBACK_SESSIONS + 5} warm-up.")
            continue

        full_records = simulate_sessions(sessions, spread_points=IG_US500_OPEN_SPREAD_POINTS)
        full_res = summarize(full_records, sessions)
        _print_result("full", full_res)
        rows.append(
            {"dataset": dataset, "meas_minutes": meas, "period": "full", **asdict(full_res)}
        )

        is_sessions, oos_sessions = _split(sessions, is_frac=args.is_frac)
        if len(oos_sessions) > LOOKBACK_SESSIONS + 5:
            # Re-simulate OOS starting cold (own warm-up) so the split is a
            # clean quick screen — not a claim of formal walk-forward (that
            # is validate_session_open_continuation.py's job).
            is_records = simulate_sessions(is_sessions, spread_points=IG_US500_OPEN_SPREAD_POINTS)
            oos_records = simulate_sessions(oos_sessions, spread_points=IG_US500_OPEN_SPREAD_POINTS)
            is_res = summarize(is_records, is_sessions)
            oos_res = summarize(oos_records, oos_sessions)
            _print_result("IS", is_res)
            _print_result("OOS", oos_res)
            rows.append(
                {"dataset": dataset, "meas_minutes": meas, "period": "IS", **asdict(is_res)}
            )
            rows.append(
                {"dataset": dataset, "meas_minutes": meas, "period": "OOS", **asdict(oos_res)}
            )
            summary_lines.append(
                f"{label}: full={full_res.return_pct:+.2f}% "
                f"OOS={oos_res.return_pct:+.2f}% (vs_bh={oos_res.vs_bh:+.2f}%) "
                f"trades={oos_res.n_trades}"
            )
        else:
            print(
                f"  (OOS split skipped: only {len(oos_sessions)} sessions after {args.is_frac:.0%} cut)"
            )
            summary_lines.append(
                f"{label}: full={full_res.return_pct:+.2f}% (no OOS split — too few sessions)"
            )
        print()

    if not rows:
        raise SystemExit("No results — backfill 15m/1h data first (see docstring).")

    args.csv.parent.mkdir(parents=True, exist_ok=True)
    with args.csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows -> {args.csv}")
    print(
        "\nThis is a quick screen only (single IS/OOS cut, own warm-up each "
        "side). Formal rolling OOS + promote/reject verdict: "
        "scripts/validate_session_open_continuation.py"
    )

    summary = "\n".join(
        [
            "Research done: session_open_continuation (US500 cash open)",
            f"spread={IG_US500_OPEN_SPREAD_POINTS}pts risk_pct={RISK_PCT:.1%}",
            *summary_lines,
            f"csv={args.csv}",
        ]
    )
    if notify_demo_research(summary, enabled=not args.no_discord):
        print("Posted summary to Discord demo webhook.", flush=True)


if __name__ == "__main__":
    main()
