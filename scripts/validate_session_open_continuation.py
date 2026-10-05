#!/usr/bin/env python3
"""Full-history + rolling-OOS validation for session-open continuation.

Fixed-parameter validation (no grid search — the design is locked in
docs/strategies/session-open-continuation.md). The whole point of this
script is to answer one question per candidate: does the rule hold up
out-of-sample, consistently, across time? Rolling windows are sized by
*session count*, not bar count, and IS is a spacer only (no parameter
fitting happens on it) — same convention as scripts/validate_session_breakout.py
adapted from bars to sessions.

Candidates (see scripts/research_session_open_continuation.py for the shared
signal/sizing/cost logic):
  15m/meas30            — primary, thin history (~60 sessions, indicative only)
  15m/meas60            — primary, thin history (~60 sessions, indicative only)
  1h/meas60 depth check — decision-grade (~3y, ~730 sessions)

Usage:
    uv run python scripts/validate_session_open_continuation.py
    uv run python scripts/validate_session_open_continuation.py --only 1h_meas60
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from research_session_open_continuation import (
    IG_US500_OPEN_SPREAD_POINTS,
    LOOKBACK_SESSIONS,
    SessionSample,
    TradeRecord,
    build_sessions,
    simulate_sessions,
    summarize,
)
from trading_platform.backtesting.result import BacktestResult, EquityPoint
from trading_platform.backtesting.walk_forward import iter_walk_forward_windows, stitch_oos_equity
from trading_platform.config.settings import Settings
from trading_platform.market_data.repository.parquet import ParquetMarketDataRepository
from trading_platform.notifications.research import notify_demo_research

_EXCHANGE = "ig"
_EPIC = "IX.D.SPTRD.IFM.IP"
_STARTING_CASH = Decimal("50000")


@dataclass(frozen=True, slots=True)
class Candidate:
    key: str
    label: str
    dataset: str
    meas_minutes: int
    is_sessions: int
    oos_sessions: int
    step_sessions: int
    decision_grade: bool


@dataclass(frozen=True, slots=True)
class CandidateOutcome:
    candidate: Candidate
    verdict: str  # "PROMOTE", "REJECT", or "INSUFFICIENT_DEPTH"
    line: str


_CANDIDATES: list[Candidate] = [
    Candidate(
        key="15m_meas30",
        label="15m/meas30",
        dataset="15m",
        meas_minutes=30,
        is_sessions=20,
        oos_sessions=10,
        step_sessions=10,
        decision_grade=False,
    ),
    Candidate(
        key="15m_meas60",
        label="15m/meas60",
        dataset="15m",
        meas_minutes=60,
        is_sessions=20,
        oos_sessions=10,
        step_sessions=10,
        decision_grade=False,
    ),
    Candidate(
        key="1h_meas60",
        label="1h/meas60 (depth check)",
        dataset="1h",
        meas_minutes=60,
        is_sessions=100,
        oos_sessions=60,
        step_sessions=80,
        decision_grade=True,
    ),
]


def _fold_equity_curve(records: list[TradeRecord]) -> tuple[EquityPoint, ...]:
    """Fresh-compounded equity curve for one fold, starting at `_STARTING_CASH`.

    Records come from the single global chronological `simulate_sessions`
    run (correct, no-lookahead trailing stats); this only rebuilds a *local*
    relative equity path for the fold's slice so `stitch_oos_equity`'s own
    rebasing (`level * point.equity / base`) composes folds correctly.
    """
    points: list[EquityPoint] = []
    equity = _STARTING_CASH
    for r in records:
        equity = equity * (Decimal(str(1.0 + r.pnl_pct / 100.0)))
        points.append(EquityPoint(timestamp=r.exit_time, equity=equity))
    return tuple(points)


def _fold_backtest_result(records: list[TradeRecord]) -> BacktestResult:
    curve = _fold_equity_curve(records)
    ending = curve[-1].equity if curve else _STARTING_CASH
    return BacktestResult(
        symbol=_EPIC,
        timeframe="session",
        starting_cash=_STARTING_CASH,
        ending_cash=ending,
        bars_processed=len(records),
        fills=(),
        total_fees_paid=Decimal("0"),
        equity_curve=curve,
        final_position=None,
    )


def _validate_one(candidate: Candidate) -> CandidateOutcome:
    settings = Settings()
    repo = ParquetMarketDataRepository(Path(settings.data_dir), exchange=_EXCHANGE)
    bars = list(repo.load_bars(_EPIC, candidate.dataset))
    if not bars:
        raise SystemExit(f"No {candidate.dataset} bars for {_EPIC} — run the Phase 0 backfill.")

    sessions: list[SessionSample] = build_sessions(bars, meas_minutes=candidate.meas_minutes)
    span_days = (bars[-1].timestamp - bars[0].timestamp) / timedelta(days=1)
    print(f"\n########## {candidate.key} ##########")
    print(
        f"Validating {candidate.label} on {_EPIC}@{candidate.dataset}: "
        f"{len(sessions)} sessions ({sessions[0].session_date if sessions else 'n/a'} -> "
        f"{sessions[-1].session_date if sessions else 'n/a'}, ~{span_days:.0f}d bar span)"
    )
    print(
        f"(spread={IG_US500_OPEN_SPREAD_POINTS}pts both fills; lookback={LOOKBACK_SESSIONS} "
        f"sessions; financing not modelled — flat every 16:00 ET)\n"
    )

    if len(sessions) <= LOOKBACK_SESSIONS + 5:
        msg = (
            f"INSUFFICIENT DEPTH: only {len(sessions)} sessions "
            f"(need > {LOOKBACK_SESSIONS + 5} for warm-up alone). Skipping — "
            f"per the plan's fallback, this candidate is deferred rather than rescued."
        )
        print(msg)
        return CandidateOutcome(candidate, "INSUFFICIENT_DEPTH", f"{candidate.label}: {msg}")

    full_records = simulate_sessions(sessions, spread_points=IG_US500_OPEN_SPREAD_POINTS)
    full_res = summarize(full_records, sessions)
    sharpe = f"{full_res.sharpe:.2f}" if full_res.sharpe is not None else "n/a"
    win = f"{full_res.win_rate * 100:.0f}%" if full_res.win_rate is not None else "n/a"
    print(
        f"Full history: ret={full_res.return_pct:+.2f}% vs_bh={full_res.vs_bh:+.2f}% "
        f"maxdd={full_res.maxdd_pct:.2f}% sharpe={sharpe} trades={full_res.n_trades} win={win}"
    )

    windows = iter_walk_forward_windows(
        len(sessions),
        is_bars=candidate.is_sessions,
        oos_bars=candidate.oos_sessions,
        step_bars=candidate.step_sessions,
    )
    if not windows:
        msg = (
            f"INSUFFICIENT DEPTH for rolling OOS ({len(sessions)} sessions, "
            f"need >= {candidate.is_sessions + candidate.oos_sessions}). "
            f"Full-history read only — treat as indicative, not a promotion basis."
        )
        print(f"\n{msg}")
        return CandidateOutcome(
            candidate,
            "INSUFFICIENT_DEPTH",
            f"{candidate.label}: full={full_res.return_pct:+.2f}% "
            f"(vs_bh={full_res.vs_bh:+.2f}%) — no rolling OOS ({msg})",
        )

    print(
        f"\nRolling OOS (fixed params, sized by session count): {len(windows)} folds  "
        f"IS_spacer={candidate.is_sessions} OOS={candidate.oos_sessions} "
        f"step={candidate.step_sessions}"
    )
    print(f"\n{'fold':>4} {'OOS session window':>26} {'oos_ret%':>9} {'oos_trades':>10} {'bh%':>8}")
    print("-" * 62)

    fold_results: list[BacktestResult] = []
    oos_returns: list[float] = []
    for fold_index, (_is_s, _is_e, oos_s, oos_e) in enumerate(windows):
        oos_sessions_slice = sessions[oos_s:oos_e]
        oos_records_slice = full_records[oos_s:oos_e]
        oos_res = summarize(oos_records_slice, oos_sessions_slice)
        oos_returns.append(oos_res.return_pct)
        fold_results.append(_fold_backtest_result(oos_records_slice))
        a = oos_sessions_slice[0].session_date
        b = oos_sessions_slice[-1].session_date
        print(
            f"{fold_index:>4} {str(a)}->{b}  {oos_res.return_pct:>+9.2f} "
            f"{oos_res.n_trades:>10} {oos_res.bh_pct:>+8.2f}",
            flush=True,
        )

    wins = sum(1 for r in oos_returns if r > 0)
    oos_summary = (
        f"OOS folds: {len(oos_returns)}  positive={wins}/{len(oos_returns)}  "
        f"mean={sum(oos_returns) / len(oos_returns):+.2f}%  "
        f"median={sorted(oos_returns)[len(oos_returns) // 2]:+.2f}%"
    )
    print(f"\n{oos_summary}")

    stitched = stitch_oos_equity(fold_results, starting_cash=_STARTING_CASH)
    stitched_ret: float | None = None
    if stitched:
        start_eq = stitched[0].equity
        end_eq = stitched[-1].equity
        stitched_ret = float((end_eq - start_eq) / start_eq * 100) if start_eq else 0.0
        print(f"Stitched OOS equity return: {stitched_ret:+.2f}%")

    consistently_positive = wins == len(oos_returns) or (
        wins / len(oos_returns) >= 0.6 and stitched_ret is not None and stitched_ret > 0
    )
    promote = full_res.return_pct > 0 and consistently_positive
    verdict = "PROMOTE" if promote else "REJECT"
    print(
        "\nVerdict rule: promote only if full-history is positive AND OOS is "
        "consistently positive (>=60% of folds + stitched OOS > 0, or all folds "
        "positive) — same discipline as validate_session_breakout.py."
    )
    print(f"-> {candidate.label}: {verdict}")

    summary = "\n".join(
        [
            f"full: ret={full_res.return_pct:+.2f}% vs_bh={full_res.vs_bh:+.2f}% "
            f"trades={full_res.n_trades}",
            oos_summary,
            (
                f"stitched OOS: {stitched_ret:+.2f}%"
                if stitched_ret is not None
                else "stitched: n/a"
            ),
            f"verdict={verdict}",
        ]
    )
    return CandidateOutcome(
        candidate, verdict, f"{candidate.label}: {summary.replace(chr(10), ' | ')}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        choices=[c.key for c in _CANDIDATES],
        default=None,
    )
    parser.add_argument("--no-discord", action="store_true")
    args = parser.parse_args()

    chosen = [c for c in _CANDIDATES if args.only is None or c.key == args.only]
    if not chosen:
        raise SystemExit("No candidates selected.")

    outcomes = [_validate_one(c) for c in chosen]

    print("\n" + "=" * 70)
    print("Phase A summary")
    print("=" * 70)
    for outcome in outcomes:
        print(f"- {outcome.line}")

    decision_grade = [o for o in outcomes if o.candidate.decision_grade]
    indicative = [o for o in outcomes if not o.candidate.decision_grade]
    if decision_grade:
        overall = "PROMOTE" if all(o.verdict == "PROMOTE" for o in decision_grade) else "REJECT"
        print(
            f"\nOverall Phase A verdict: {overall} "
            f"(driven by decision-grade candidate(s): "
            f"{', '.join(o.candidate.label for o in decision_grade)})"
        )
        if overall == "REJECT" and any(o.verdict == "PROMOTE" for o in indicative):
            print(
                "Note: thin/indicative candidate(s) showed a PROMOTE read on a "
                "small sample, but the decision-grade read rejects. Per the "
                "reject rule, the deeper/decision-grade result governs — this "
                "is a clean negative, not a signal to widen the design to "
                "chase the thin-sample result."
            )
    print(
        "\nReject rule: if the decision-grade (1h/meas60) read fails or is "
        "inconsistent, stop — do not widen measurement windows, add markets, "
        "or add exit logic to rescue it. A clean negative is an accepted, "
        "useful outcome. See docs/strategies/session-open-continuation.md."
    )

    summary = "\n".join(
        [
            "Validation done: session_open_continuation (US500 cash open)",
            *[o.line for o in outcomes],
        ]
    )
    if notify_demo_research(summary, enabled=not args.no_discord):
        print("Posted summary to Discord demo webhook.", flush=True)


if __name__ == "__main__":
    main()
