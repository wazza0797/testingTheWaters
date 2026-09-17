#!/usr/bin/env python3
"""IG FX pairs / residual mean-reversion research.

Platform backtests are single-symbol today, so this script stays research-only:
align two Yahoo-backed IG FX legs, estimate a *causal* rolling hedge ratio on
log prices, trade the residual z-score, and charge the **sum of both legs'
spread floors** on every position change. Residual units are not a tradeable
CFD; treat results as indicative until a real dual-leg fill path exists.

Pairs (textbook residual MR):
  eurusd/gbpusd, audusd/nzdusd, eurusd/eurchf, eurusd/audusd

Signal (next-bar entry, 1-bar latency):
  long residual when z <= -z_entry; short when z >= +z_entry;
  flatten when |z| <= z_exit.

PnL approx (log-space residual return while holding ±1):
  r_t = (Δlog A − β_t · Δlog B) · position_{t-1}
  cost on |Δposition| = |Δpos| · (spread_a + spread_b) bps of equity

Usage:
    uv run python scripts/research_ig_fx_pairs.py
    uv run python scripts/research_ig_fx_pairs.py --grid quick --timeframes 1h
    uv run python scripts/research_ig_fx_pairs.py --pairs eurusd_gbpusd,audusd_nzdusd
"""

from __future__ import annotations

import argparse
import csv
import itertools
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from trading_platform.config.loader import load_config
from trading_platform.config.settings import Settings
from trading_platform.domain.models.bar import Bar
from trading_platform.market_data.repository.parquet import ParquetMarketDataRepository
from trading_platform.notifications.research import notify_demo_research

# Overlay key → (pair short-name used in labels)
_LEG_OVERLAYS: dict[str, str] = {
    "eurusd": "ig-eurusd",
    "gbpusd": "ig-gbpusd",
    "audusd": "ig-audusd",
    "nzdusd": "ig-nzdusd",
    "eurchf": "ig-eurchf",
}

_DEFAULT_PAIRS: tuple[str, ...] = (
    "eurusd_gbpusd",
    "audusd_nzdusd",
    "eurusd_eurchf",
    "eurusd_audusd",
)


@dataclass(frozen=True, slots=True)
class Leg:
    key: str
    overlay: str
    symbol: str
    spread_bps: float


@dataclass(frozen=True, slots=True)
class PairSpec:
    name: str
    leg_a: Leg
    leg_b: Leg

    @property
    def cost_bps(self) -> float:
        return self.leg_a.spread_bps + self.leg_b.spread_bps


@dataclass(frozen=True, slots=True)
class Case:
    window: int
    z_entry: float
    z_exit: float
    label: str


def _load_leg(key: str) -> Leg:
    overlay = _LEG_OVERLAYS[key]
    cfg = load_config(overlay=overlay)
    spread = cfg.backtest.spread_bps
    if spread is None:
        raise SystemExit(f"{overlay}: spread_bps required for pairs cost budget")
    return Leg(key=key, overlay=overlay, symbol=cfg.trading.symbol, spread_bps=float(spread))


def _parse_pair(name: str) -> PairSpec:
    parts = name.split("_")
    if len(parts) != 2 or parts[0] not in _LEG_OVERLAYS or parts[1] not in _LEG_OVERLAYS:
        raise SystemExit(f"Unknown pair {name!r}; expected legA_legB from {sorted(_LEG_OVERLAYS)}")
    return PairSpec(name=name, leg_a=_load_leg(parts[0]), leg_b=_load_leg(parts[1]))


def _closes(bars: list[Bar]) -> tuple[np.ndarray, np.ndarray]:
    """Return parallel float64 arrays of unix-ns timestamps and closes."""
    ts = np.fromiter(
        (int(b.timestamp.timestamp() * 1e9) for b in bars), dtype=np.int64, count=len(bars)
    )
    px = np.fromiter((float(b.close) for b in bars), dtype=np.float64, count=len(bars))
    return ts, px


def _align(
    ts_a: np.ndarray, px_a: np.ndarray, ts_b: np.ndarray, px_b: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Inner-join on timestamp; return aligned close series."""
    # Assume each series is sorted unique timestamps (parquet OHLCV is).
    idx_a = {int(t): i for i, t in enumerate(ts_a)}
    out_a: list[float] = []
    out_b: list[float] = []
    for i, t in enumerate(ts_b):
        j = idx_a.get(int(t))
        if j is None:
            continue
        out_a.append(float(px_a[j]))
        out_b.append(float(px_b[i]))
    if len(out_a) < 50:
        return np.asarray([], dtype=np.float64), np.asarray([], dtype=np.float64)
    return np.asarray(out_a, dtype=np.float64), np.asarray(out_b, dtype=np.float64)


def _rolling_beta(log_a: np.ndarray, log_b: np.ndarray, window: int) -> np.ndarray:
    """Causal rolling OLS slope of log_a on log_b (β_t uses bars [t-window+1, t])."""
    n = len(log_a)
    beta = np.full(n, np.nan, dtype=np.float64)
    # Running sums for O(n) rolling cov/var.
    for t in range(window - 1, n):
        a = log_a[t - window + 1 : t + 1]
        b = log_b[t - window + 1 : t + 1]
        b_mean = b.mean()
        a_mean = a.mean()
        var_b = np.dot(b - b_mean, b - b_mean)
        if var_b <= 1e-18:
            continue
        cov = np.dot(b - b_mean, a - a_mean)
        beta[t] = cov / var_b
    return beta


def _rolling_z(residual: np.ndarray, window: int) -> np.ndarray:
    n = len(residual)
    z = np.full(n, np.nan, dtype=np.float64)
    for t in range(window - 1, n):
        w = residual[t - window + 1 : t + 1]
        mu = w.mean()
        sd = w.std(ddof=1)
        if sd <= 1e-12 or math.isnan(sd):
            continue
        z[t] = (residual[t] - mu) / sd
    return z


def _max_drawdown(equity: np.ndarray) -> float:
    peak = equity[0]
    max_dd = 0.0
    for x in equity:
        if x > peak:
            peak = x
        dd = (x - peak) / peak if peak else 0.0
        if dd < max_dd:
            max_dd = dd
    return max_dd * 100.0


def _sharpe_daily(equity: np.ndarray, timestamps_per_day: float) -> float | None:
    if len(equity) < 3 or timestamps_per_day <= 0:
        return None
    rets = np.diff(equity) / equity[:-1]
    # Aggregate roughly to daily by step.
    step = max(1, int(round(timestamps_per_day)))
    daily = []
    for i in range(0, len(rets), step):
        chunk = rets[i : i + step]
        daily.append(float(np.prod(1.0 + chunk) - 1.0))
    if len(daily) < 5:
        return None
    arr = np.asarray(daily, dtype=np.float64)
    sd = arr.std(ddof=1)
    if sd <= 1e-18:
        return None
    return float(arr.mean() / sd * math.sqrt(252.0))


def simulate(
    px_a: np.ndarray,
    px_b: np.ndarray,
    *,
    window: int,
    z_entry: float,
    z_exit: float,
    cost_bps: float,
    starting_cash: float,
    bars_per_day: float,
) -> dict[str, Any]:
    log_a = np.log(px_a)
    log_b = np.log(px_b)
    beta = _rolling_beta(log_a, log_b, window)
    residual = log_a - beta * log_b
    z = _rolling_z(residual, window)

    n = len(px_a)
    equity = np.empty(n, dtype=np.float64)
    equity[0] = starting_cash
    position = 0  # -1 short residual, 0 flat, +1 long residual
    trips = 0
    wins = 0
    entry_equity = starting_cash
    trade_pnls: list[float] = []

    # Signal on bar t close → position applies to return from t → t+1 (1-bar latency).
    for t in range(1, n):
        # Mark-to-market prior position on residual return over [t-1, t].
        b = beta[t - 1]
        if position != 0 and not math.isnan(b):
            d_res = (log_a[t] - log_a[t - 1]) - b * (log_b[t] - log_b[t - 1])
            equity[t] = equity[t - 1] * (1.0 + position * d_res)
        else:
            equity[t] = equity[t - 1]

        # Decide target from z at t-1 (known before bar t open / this close).
        z_sig = z[t - 1]
        target = position
        if not math.isnan(z_sig):
            if position == 0:
                if z_sig <= -z_entry:
                    target = 1
                elif z_sig >= z_entry:
                    target = -1
            else:
                if abs(z_sig) <= z_exit:
                    target = 0
                elif position == 1 and z_sig >= z_entry:
                    target = -1  # flip
                elif position == -1 and z_sig <= -z_entry:
                    target = 1

        if target != position:
            # Charge dual-leg spread on each unit of position change.
            delta = abs(target - position)
            cost = equity[t] * (cost_bps / 10_000.0) * delta
            equity[t] -= cost
            # Round-trip accounting: count a completed trip when we return to flat
            # or flip (flip closes one and opens another → 1 close).
            if position != 0 and (target == 0 or (target != 0 and target != position)):
                pnl = equity[t] - entry_equity
                # Approximate: use equity before this bar's residual move for entry
                # baseline already tracked; pnl includes last mark + cost.
                trade_pnls.append(pnl)
                trips += 1
                if pnl > 0:
                    wins += 1
            if (
                target != 0
                and position == 0
                or target != 0
                and position != 0
                and target != position
            ):
                entry_equity = equity[t]
            position = target

    total_ret = (equity[-1] / starting_cash - 1.0) * 100.0
    bh_a = (px_a[-1] / px_a[0] - 1.0) * 100.0
    maxdd = _max_drawdown(equity)
    sharpe = _sharpe_daily(equity, bars_per_day)
    win_rate = (wins / trips) if trips else None
    return {
        "return_pct": total_ret,
        "maxdd_pct": maxdd,
        "sharpe": sharpe,
        "trips": trips,
        "win_rate": win_rate,
        "bh_a_pct": bh_a,
        "vs_bh_a": total_ret - bh_a,
        "end_equity": float(equity[-1]),
    }


def build_grid(density: str) -> list[Case]:
    if density == "quick":
        windows = [48, 96, 192]
        z_entries = [1.5, 2.0, 2.5]
        z_exits = [0.0, 0.25, 0.5]
    elif density == "full":
        windows = [24, 48, 96, 144, 240]
        z_entries = [1.0, 1.5, 2.0, 2.5, 3.0]
        z_exits = [0.0, 0.25, 0.5, 0.75]
    else:  # medium
        windows = [48, 96, 144, 240]
        z_entries = [1.5, 2.0, 2.5]
        z_exits = [0.0, 0.25, 0.5]

    cases: list[Case] = []
    for w, ze, zx in itertools.product(windows, z_entries, z_exits):
        if zx >= ze:
            continue
        cases.append(Case(window=w, z_entry=ze, z_exit=zx, label=f"w{w}|ze{ze:g}|zx{zx:g}"))
    return cases


def _parse_csv(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _trim_pair(
    px_a: np.ndarray, px_b: np.ndarray, max_bars: int | None
) -> tuple[np.ndarray, np.ndarray]:
    if max_bars is None or len(px_a) <= max_bars:
        return px_a, px_b
    return px_a[-max_bars:], px_b[-max_bars:]


def _bars_per_day(timeframe: str) -> float:
    if timeframe == "1h":
        return 24.0 * 5.0 / 7.0  # ~17 FX hours/day average
    if timeframe == "4h":
        return 6.0 * 5.0 / 7.0
    if timeframe == "15m":
        return 96.0 * 5.0 / 7.0
    return 24.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", default=",".join(_DEFAULT_PAIRS))
    parser.add_argument("--timeframes", default="1h,4h")
    parser.add_argument("--grid", choices=("quick", "medium", "full"), default="quick")
    parser.add_argument("--max-bars", type=int, default=2500)
    parser.add_argument("--starting-cash", type=float, default=10_000.0)
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--min-trips", type=int, default=10)
    parser.add_argument(
        "--csv",
        type=Path,
        default=Path("data/research/ig_fx_pairs.csv"),
    )
    parser.add_argument("--no-discord", action="store_true")
    args = parser.parse_args()

    pair_names = _parse_csv(args.pairs)
    timeframes = _parse_csv(args.timeframes)
    cases = build_grid(args.grid)
    pairs = [_parse_pair(name) for name in pair_names]

    settings = Settings()
    repo = ParquetMarketDataRepository(Path(settings.data_dir), exchange="ig")

    print(
        "IG FX pairs residual MR — research-only synthetic residual; "
        "dual-leg spread charged on position changes. No overnight funding."
    )
    print(f"grid={args.grid} cases/pair/tf={len(cases)} pairs={len(pairs)} tfs={timeframes}")
    print("Varies: hedge/z window, z_entry, z_exit. Fixed: rolling OLS β, 1-bar latency.\n")

    # Preload aligned series per pair/tf.
    series: dict[tuple[str, str], tuple[np.ndarray, np.ndarray, PairSpec]] = {}
    for pair in pairs:
        for tf in timeframes:
            bars_a = list(repo.load_bars(pair.leg_a.symbol, tf))
            bars_b = list(repo.load_bars(pair.leg_b.symbol, tf))
            if not bars_a or not bars_b:
                print(f"# {pair.name}@{tf}: missing bars (A={len(bars_a)} B={len(bars_b)})")
                continue
            ts_a, px_a = _closes(bars_a)
            ts_b, px_b = _closes(bars_b)
            aligned_a, aligned_b = _align(ts_a, px_a, ts_b, px_b)
            aligned_a, aligned_b = _trim_pair(aligned_a, aligned_b, args.max_bars)
            if len(aligned_a) < 200:
                print(f"# {pair.name}@{tf}: only {len(aligned_a)} aligned bars — skip")
                continue
            # Approximate span from bar count / bars_per_day.
            span = len(aligned_a) / _bars_per_day(tf)
            print(
                f"# {pair.name}@{tf}: aligned={len(aligned_a)} (~{span:.0f}d) "
                f"cost_bps={pair.cost_bps:.1f} "
                f"({pair.leg_a.symbol} / {pair.leg_b.symbol})"
            )
            series[(pair.name, tf)] = (aligned_a, aligned_b, pair)

    jobs = [(name_tf[0], name_tf[1], case, series[name_tf]) for name_tf in series for case in cases]
    print(f"\njobs={len(jobs)} starting_cash={args.starting_cash:g}\n", flush=True)

    rows: list[dict[str, Any]] = []
    for i, (pair_name, tf, case, (px_a, px_b, pair)) in enumerate(jobs, start=1):
        metrics = simulate(
            px_a,
            px_b,
            window=case.window,
            z_entry=case.z_entry,
            z_exit=case.z_exit,
            cost_bps=pair.cost_bps,
            starting_cash=args.starting_cash,
            bars_per_day=_bars_per_day(tf),
        )
        row = {
            "pair": pair_name,
            "tf": tf,
            "label": case.label,
            "window": case.window,
            "z_entry": case.z_entry,
            "z_exit": case.z_exit,
            "cost_bps": pair.cost_bps,
            **metrics,
        }
        rows.append(row)
        if i == 1 or i % 50 == 0 or i == len(jobs):
            print(
                f"  … {i}/{len(jobs)} {pair_name}/{tf} {case.label} "
                f"ret={metrics['return_pct']:+.2f}% trips={metrics['trips']}",
                flush=True,
            )

    ranked = [r for r in rows if r["trips"] >= args.min_trips]
    ranked.sort(key=lambda r: (r["return_pct"], r["sharpe"] or -99.0), reverse=True)

    header = (
        f"{'pair':<16} {'tf':<4} {'return%':>8} {'vs_bhA':>8} {'maxdd%':>8} "
        f"{'sharpe':>7} {'trips':>5} {'win%':>6} {'bhA%':>7}  label"
    )
    print(f"\n=== Top {args.top} by return (min_trips>={args.min_trips}) ===")
    print(header)
    print("-" * len(header))
    for r in ranked[: args.top]:
        sharpe = f"{r['sharpe']:.2f}" if r["sharpe"] is not None else "n/a"
        win = f"{r['win_rate'] * 100:.1f}" if r["win_rate"] is not None else "n/a"
        print(
            f"{r['pair']:<16} {r['tf']:<4} {r['return_pct']:>8.2f} {r['vs_bh_a']:>8.2f} "
            f"{r['maxdd_pct']:>8.2f} {sharpe:>7} {r['trips']:>5} {win:>6} "
            f"{r['bh_a_pct']:>7.2f}  {r['label']}"
        )

    print("\n=== Best per pair/tf ===")
    best_lines: list[str] = []
    for pair in pairs:
        for tf in timeframes:
            subset = [r for r in ranked if r["pair"] == pair.name and r["tf"] == tf]
            if not subset:
                line = f"{pair.name}/{tf}: none"
                print(line)
                best_lines.append(line)
                continue
            best = subset[0]
            line = (
                f"{pair.name}/{tf}: ret={best['return_pct']:+.2f}% "
                f"vs_bhA={best['vs_bh_a']:+.2f}% trips={best['trips']}  {best['label']}"
            )
            print(line)
            best_lines.append(line)

    beat_bh = [r for r in ranked if r["vs_bh_a"] > 0 and r["return_pct"] > 0]
    print(f"\nPositive return AND beat BH(A): {len(beat_bh)}/{len(ranked)} ranked")

    args.csv.parent.mkdir(parents=True, exist_ok=True)
    with args.csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else [])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows -> {args.csv}")

    top_lines = [
        (
            f"{r['pair']}/{r['tf']} ret={r['return_pct']:+.2f}% "
            f"vs_bhA={r['vs_bh_a']:+.2f}% trips={r['trips']} {r['label']}"
        )
        for r in ranked[:10]
    ]
    summary = "\n".join(
        [
            f"Research done: ig_fx_pairs grid={args.grid}",
            f"pairs={','.join(pair_names)} tfs={','.join(timeframes)} jobs={len(jobs)}",
            "Caveat: synthetic residual + sum-of-leg spreads; not dual-leg fills.",
            "Top (by return):",
            *(top_lines or ["(none met min_trips)"]),
            "Best per pair/tf:",
            *best_lines,
            f"beat_bhA_and_green={len(beat_bh)} csv={args.csv}",
        ]
    )
    if notify_demo_research(summary, enabled=not args.no_discord):
        print("Posted summary to Discord demo webhook.", flush=True)


if __name__ == "__main__":
    main()
