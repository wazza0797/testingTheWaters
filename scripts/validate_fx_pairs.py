#!/usr/bin/env python3
"""Full-history + rolling-OOS validation for FX pairs residual screen hits.

Candidates (quick screen on trailing 2500 bars; green *and* beat BH of leg A):

  Primary cluster — EURUSD/EURCHF 1h:
    w48|ze1.5|zx0.25, w48|ze1.5|zx0, w48|ze1.5|zx0.5

  Also check best other-pair 1h hits:
    eurusd_gbpusd w48|ze2.5|zx0
    eurusd_audusd w48|ze2.5|zx0

Uses the same research-only residual simulator as research_ig_fx_pairs.py
(synthetic residual + sum-of-leg spreads; not dual-leg fills).

Usage:
    uv run python scripts/validate_fx_pairs.py
    uv run python scripts/validate_fx_pairs.py --only eurusd_eurchf --no-discord
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from trading_platform.config.settings import Settings
from trading_platform.market_data.repository.parquet import ParquetMarketDataRepository
from trading_platform.notifications.research import notify_demo_research

_RESEARCH = Path(__file__).resolve().with_name("research_ig_fx_pairs.py")
_SPEC = importlib.util.spec_from_file_location("research_ig_fx_pairs", _RESEARCH)
assert _SPEC is not None and _SPEC.loader is not None
_pairs = importlib.util.module_from_spec(_SPEC)
sys.modules["research_ig_fx_pairs"] = _pairs
_SPEC.loader.exec_module(_pairs)

# ~90d IS spacer / ~30d OOS at ~17 FX 1h bars/day.
_IS_BARS = 1500
_OOS_BARS = 500
_STEP_BARS = 1000
_STARTING_CASH = 10_000.0


@dataclass(frozen=True, slots=True)
class Candidate:
    key: str
    pair: str
    timeframe: str
    window: int
    z_entry: float
    z_exit: float

    @property
    def label(self) -> str:
        return f"w{self.window}|ze{self.z_entry:g}|zx{self.z_exit:g}"


_CANDIDATES: list[Candidate] = [
    Candidate("eurchf_ze15_zx025", "eurusd_eurchf", "1h", 48, 1.5, 0.25),
    Candidate("eurchf_ze15_zx0", "eurusd_eurchf", "1h", 48, 1.5, 0.0),
    Candidate("eurchf_ze15_zx05", "eurusd_eurchf", "1h", 48, 1.5, 0.5),
    Candidate("gbpusd_ze25_zx0", "eurusd_gbpusd", "1h", 48, 2.5, 0.0),
    Candidate("audusd_ze25_zx0", "eurusd_audusd", "1h", 48, 2.5, 0.0),
]


def _align_with_ts(
    ts_a: np.ndarray,
    px_a: np.ndarray,
    ts_b: np.ndarray,
    px_b: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    idx_a = {int(t): i for i, t in enumerate(ts_a)}
    out_ts: list[int] = []
    out_a: list[float] = []
    out_b: list[float] = []
    for i, t in enumerate(ts_b):
        j = idx_a.get(int(t))
        if j is None:
            continue
        out_ts.append(int(t))
        out_a.append(float(px_a[j]))
        out_b.append(float(px_b[i]))
    return (
        np.asarray(out_ts, dtype=np.int64),
        np.asarray(out_a, dtype=np.float64),
        np.asarray(out_b, dtype=np.float64),
    )


def _iter_oos_windows(n: int) -> list[tuple[int, int]]:
    """Return (oos_start, oos_end) slices; IS spacer only positions the roll."""
    windows: list[tuple[int, int]] = []
    start = _IS_BARS
    while start + _OOS_BARS <= n:
        windows.append((start, start + _OOS_BARS))
        start += _STEP_BARS
    return windows


def _ns_to_date(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, tz=UTC).date().isoformat()


def _print_metrics(label: str, m: dict[str, Any]) -> None:
    sharpe = f"{m['sharpe']:.2f}" if m["sharpe"] is not None else "n/a"
    win = f"{m['win_rate'] * 100:.1f}%" if m["win_rate"] is not None else "n/a"
    print(f"=== {label} ===")
    print(
        f"return={m['return_pct']:+.2f}%  maxdd={m['maxdd_pct']:.2f}%  "
        f"sharpe={sharpe}  trips={m['trips']}  win={win}  "
        f"bhA={m['bh_a_pct']:+.2f}%  vs_bhA={m['vs_bh_a']:+.2f}%"
    )
    print()


def _validate_one(candidate: Candidate, *, discord: bool) -> str:
    pair = _pairs._parse_pair(candidate.pair)
    settings = Settings()
    repo = ParquetMarketDataRepository(Path(settings.data_dir), exchange="ig")
    bars_a = list(repo.load_bars(pair.leg_a.symbol, candidate.timeframe))
    bars_b = list(repo.load_bars(pair.leg_b.symbol, candidate.timeframe))
    if not bars_a or not bars_b:
        raise SystemExit(f"Missing bars for {candidate.pair}@{candidate.timeframe}")

    ts_a, px_a = _pairs._closes(bars_a)
    ts_b, px_b = _pairs._closes(bars_b)
    ts, aligned_a, aligned_b = _align_with_ts(ts_a, px_a, ts_b, px_b)
    if len(aligned_a) < _IS_BARS + _OOS_BARS:
        raise SystemExit(f"Need >= {_IS_BARS + _OOS_BARS} aligned bars; got {len(aligned_a)}")

    span_days = (ts[-1] - ts[0]) / 1e9 / 86400.0
    print(
        f"\n########## {candidate.key} ##########\n"
        f"Validating {candidate.pair}@{candidate.timeframe} {candidate.label}: "
        f"{len(aligned_a)} aligned bars "
        f"({_ns_to_date(int(ts[0]))} -> {_ns_to_date(int(ts[-1]))}, ~{span_days:.0f}d)"
    )
    print(
        f"(cost_bps={pair.cost_bps:.1f} = {pair.leg_a.spread_bps:g}+{pair.leg_b.spread_bps:g}; "
        "synthetic residual; overnight funding not modelled)\n"
    )

    full = _pairs.simulate(
        aligned_a,
        aligned_b,
        window=candidate.window,
        z_entry=candidate.z_entry,
        z_exit=candidate.z_exit,
        cost_bps=pair.cost_bps,
        starting_cash=_STARTING_CASH,
        bars_per_day=_pairs._bars_per_day(candidate.timeframe),
    )
    _print_metrics("Full history", full)

    windows = _iter_oos_windows(len(aligned_a))
    print(
        f"Rolling OOS (fixed params): {len(windows)} folds  "
        f"IS_spacer={_IS_BARS} OOS={_OOS_BARS} step={_STEP_BARS}"
    )
    print(f"\n{'fold':>4} {'OOS window':>24} {'oos_ret%':>9} {'oos_trips':>9} {'bhA%':>8}")
    print("-" * 60)

    oos_returns: list[float] = []
    stitched = _STARTING_CASH
    for fold_index, (oos_s, oos_e) in enumerate(windows):
        # OOS-only simulate (cold-start β/z inside the fold) — same gate style as
        # validate_fx_mean_rev / walk-forward fixed-param folds.
        m = _pairs.simulate(
            aligned_a[oos_s:oos_e],
            aligned_b[oos_s:oos_e],
            window=candidate.window,
            z_entry=candidate.z_entry,
            z_exit=candidate.z_exit,
            cost_bps=pair.cost_bps,
            starting_cash=_STARTING_CASH,
            bars_per_day=_pairs._bars_per_day(candidate.timeframe),
        )
        oos_returns.append(m["return_pct"])
        stitched *= 1.0 + m["return_pct"] / 100.0
        print(
            f"{fold_index:>4} {_ns_to_date(int(ts[oos_s]))}->{_ns_to_date(int(ts[oos_e - 1]))}  "
            f"{m['return_pct']:>+9.2f} {m['trips']:>9} {m['bh_a_pct']:>+8.2f}",
            flush=True,
        )

    oos_summary = "OOS folds: n/a"
    stitched_ret = (stitched / _STARTING_CASH - 1.0) * 100.0 if oos_returns else None
    if oos_returns:
        wins = sum(1 for r in oos_returns if r > 0)
        oos_summary = (
            f"OOS folds: {len(oos_returns)}  positive={wins}/{len(oos_returns)}  "
            f"mean={sum(oos_returns) / len(oos_returns):+.2f}%  "
            f"median={sorted(oos_returns)[len(oos_returns) // 2]:+.2f}%"
        )
        print(f"\n{oos_summary}")
        print(f"Stitched OOS equity return: {stitched_ret:+.2f}%")

    print(
        "\nVerdict rule: do not promote unless OOS is consistently positive "
        "and preferably competitive with buy-and-hold of leg A."
    )

    summary_lines = [
        f"Validation done: fx_pairs {candidate.key} {candidate.pair}@{candidate.timeframe}",
        f"label={candidate.label}",
        (
            f"full: ret={full['return_pct']:+.2f}% maxdd={full['maxdd_pct']:.2f}% "
            f"trips={full['trips']} bhA={full['bh_a_pct']:+.2f}% "
            f"vs_bhA={full['vs_bh_a']:+.2f}%"
        ),
        oos_summary,
    ]
    if stitched_ret is not None:
        summary_lines.append(f"stitched OOS: {stitched_ret:+.2f}%")
    summary = "\n".join(summary_lines)
    if notify_demo_research(summary, enabled=discord):
        print("Posted summary to Discord demo webhook.", flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        choices=("eurusd_eurchf", "eurusd_gbpusd", "eurusd_audusd"),
        default=None,
        help="Validate candidates for one pair (default: all listed hits).",
    )
    parser.add_argument("--no-discord", action="store_true")
    args = parser.parse_args()

    chosen = [c for c in _CANDIDATES if args.only is None or c.pair == args.only]
    if not chosen:
        raise SystemExit("No candidates selected.")

    for candidate in chosen:
        _validate_one(candidate, discord=not args.no_discord)


if __name__ == "__main__":
    main()
