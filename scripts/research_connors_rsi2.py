#!/usr/bin/env python3
"""Connors-style RSI(2) short-term index reversal — honest daily research.

Hypothesis (retail-accessible, documented):
  In a bull regime (close > SMA200), buy panic dips when RSI(2) is extremely
  oversold; exit when price reclaims SMA5. Long-only. Provide liquidity into
  fear — not a prediction engine.

Critical research rules (from the strategy brief):
  - Signal source = Yahoo daily cash index (^GSPC / ^FTSE), NOT IG's own daily
    candle. IG will not match these RSI readings.
  - Default fills = **next bar open** (signal at close is not tradeable).
  - All stops/exits are **close-based** (no ^GSPC intraday stop fiction).
  - Optional add-on unit / pyramiding is **not** modelled (platform: one position).
  - Overnight financing + dividends are **not** in the default cost model;
    pass --financing-bps-per-day if you have IG's current debit figure.
  - ATR is used for **sizing only**, not as a stop.

Usage:
    uv run python scripts/backfill_ig_external.py --yahoo '^GSPC' \\
        --epic IX.D.SPTRD.IFM.IP --timeframe 1d
    uv run python scripts/research_connors_rsi2.py
    uv run python scripts/research_connors_rsi2.py --fill next_open --rsi-thresholds 5,10,15
"""

from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from trading_platform.config.loader import load_config
from trading_platform.config.settings import Settings
from trading_platform.domain.models.bar import Bar
from trading_platform.indicators.atr import compute_atr
from trading_platform.indicators.rsi import compute_rsi
from trading_platform.indicators.sma import compute_sma
from trading_platform.market_data.repository.parquet import ParquetMarketDataRepository
from trading_platform.notifications.research import notify_demo_research

FillMode = Literal["next_open", "signal_close"]

_MARKETS: dict[str, tuple[str, str, str]] = {
    # overlay -> (yahoo note, epic, default spread from overlay)
    "ig-us500": ("^GSPC", "IX.D.SPTRD.IFM.IP", "ig-us500"),
    "ig-ftse": ("^FTSE", "IX.D.FTSE.DAILY.IP", "ig-ftse"),
}


@dataclass(frozen=True, slots=True)
class SimResult:
    return_pct: float
    maxdd_pct: float
    sharpe: float | None
    trips: int
    win_rate: float | None
    avg_hold_days: float | None
    bh_pct: float
    vs_bh: float
    avg_trade_pct: float | None


def _bars_to_frame(bars: list[Bar]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts": [b.timestamp for b in bars],
            "open": [float(b.open) for b in bars],
            "high": [float(b.high) for b in bars],
            "low": [float(b.low) for b in bars],
            "close": [float(b.close) for b in bars],
        }
    )


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


def _sharpe(daily_rets: list[float]) -> float | None:
    if len(daily_rets) < 30:
        return None
    arr = np.asarray(daily_rets, dtype=np.float64)
    sd = arr.std(ddof=1)
    if sd <= 1e-18:
        return None
    return float(arr.mean() / sd * math.sqrt(252.0))


def simulate(
    df: pd.DataFrame,
    *,
    rsi_threshold: float,
    sma_regime: int,
    sma_exit: int,
    time_stop_days: int,
    risk_pct: float,
    atr_stop_mult: float,
    atr_period: int,
    spread_bps: float,
    financing_bps_per_day: float,
    fill: FillMode,
    starting_cash: float,
) -> SimResult:
    """Close-signal / next-open (or optimistic signal-close) simulator."""
    close = df["close"]
    high = df["high"]
    low = df["low"]
    rsi = compute_rsi(close, period=2)
    sma200 = compute_sma(close, period=sma_regime)
    sma5 = compute_sma(close, period=sma_exit)
    atr = compute_atr(high, low, close, period=atr_period)

    open_ = df["open"].to_numpy(dtype=np.float64)
    close_a = close.to_numpy(dtype=np.float64)
    rsi_a = rsi.to_numpy(dtype=np.float64)
    sma200_a = sma200.to_numpy(dtype=np.float64)
    sma5_a = sma5.to_numpy(dtype=np.float64)
    atr_a = atr.to_numpy(dtype=np.float64)
    n = len(df)

    cash = starting_cash
    qty = 0.0
    entry_px = 0.0
    entry_i = -1
    equity_curve: list[float] = []
    daily_rets: list[float] = []
    trips = 0
    wins = 0
    holds: list[int] = []
    trade_rets: list[float] = []

    pending_enter = False
    pending_exit = False

    def _equity(px: float) -> float:
        return cash + qty * px

    def _do_enter(i: int, px: float) -> None:
        nonlocal cash, qty, entry_px, entry_i
        if qty > 0 or px <= 0 or atr_a[i] != atr_a[i] or atr_a[i] <= 0:
            return
        eq = _equity(px)
        risk_cash = eq * risk_pct
        stop_dist = atr_stop_mult * atr_a[i]
        raw_qty = risk_cash / stop_dist
        raw_qty = min(raw_qty, (eq * 0.95) / px)
        if raw_qty <= 0:
            return
        notional = raw_qty * px
        spread = notional * (spread_bps / 10_000.0)
        if cash < notional + spread:
            raw_qty = max(0.0, (cash - spread) / px)
            notional = raw_qty * px
            spread = notional * (spread_bps / 10_000.0)
        if raw_qty <= 0:
            return
        cash -= notional + spread
        qty = raw_qty
        entry_px = px
        entry_i = i

    def _do_exit(i: int, px: float) -> None:
        nonlocal cash, qty, entry_px, entry_i, trips, wins
        if qty <= 0 or px <= 0:
            return
        notional = qty * px
        spread = notional * (spread_bps / 10_000.0)
        cash += notional - spread
        ret = (px - entry_px) / entry_px if entry_px else 0.0
        trade_rets.append(ret)
        holds.append(i - entry_i)
        trips += 1
        if ret > 0:
            wins += 1
        qty = 0.0
        entry_px = 0.0
        entry_i = -1

    for i in range(n):
        if qty > 0 and i > 0 and financing_bps_per_day > 0:
            cash -= qty * open_[i] * (financing_bps_per_day / 10_000.0)

        if fill == "next_open":
            if pending_exit:
                _do_exit(i, open_[i])
                pending_exit = False
            if pending_enter and qty == 0.0:
                _do_enter(i, open_[i])
                pending_enter = False

        eq_close = _equity(close_a[i])
        if equity_curve:
            daily_rets.append(eq_close / equity_curve[-1] - 1.0)
        equity_curve.append(eq_close)

        if not (rsi_a[i] == rsi_a[i] and sma200_a[i] == sma200_a[i] and sma5_a[i] == sma5_a[i]):
            continue

        in_bull = close_a[i] > sma200_a[i]
        want_exit = False
        want_enter = False

        if qty > 0:
            held = i - entry_i
            if close_a[i] < sma200_a[i] or close_a[i] > sma5_a[i] or held >= time_stop_days:
                want_exit = True
        elif in_bull and rsi_a[i] < rsi_threshold:
            prev = rsi_a[i - 1] if i > 0 else float("nan")
            if prev != prev or prev >= rsi_threshold:
                want_enter = True

        if fill == "signal_close":
            if want_exit and qty > 0:
                _do_exit(i, close_a[i])
            if want_enter and qty == 0.0:
                _do_enter(i, close_a[i])
        else:
            if want_exit and qty > 0:
                pending_exit = True
                pending_enter = False
            elif want_enter and qty == 0.0:
                pending_enter = True

    if qty > 0:
        _do_exit(n - 1, close_a[-1])

    eq = np.asarray(equity_curve, dtype=np.float64)
    total_ret = (eq[-1] / starting_cash - 1.0) * 100.0
    bh = (close_a[-1] / close_a[0] - 1.0) * 100.0
    win_rate = (wins / trips) if trips else None
    avg_hold = (sum(holds) / len(holds)) if holds else None
    avg_trade = (sum(trade_rets) / len(trade_rets) * 100.0) if trade_rets else None
    return SimResult(
        return_pct=total_ret,
        maxdd_pct=_max_drawdown(eq),
        sharpe=_sharpe(daily_rets),
        trips=trips,
        win_rate=win_rate,
        avg_hold_days=avg_hold,
        bh_pct=bh,
        vs_bh=total_ret - bh,
        avg_trade_pct=avg_trade,
    )


def _slice_by_date(df: pd.DataFrame, start: date | None, end: date | None) -> pd.DataFrame:
    out = df
    if start is not None:
        out = out[out["ts"].dt.date >= start]
    if end is not None:
        out = out[out["ts"].dt.date <= end]
    return out.reset_index(drop=True)


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    return date.fromisoformat(raw)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overlays", default="ig-us500,ig-ftse")
    parser.add_argument(
        "--fill",
        choices=("next_open", "signal_close"),
        default="next_open",
        help="next_open = honest; signal_close = optimistic diagnostic.",
    )
    parser.add_argument("--rsi-thresholds", default="5,10,15")
    parser.add_argument("--risk-pct", type=float, default=0.01, help="Equity risk per trade.")
    parser.add_argument(
        "--atr-stop-mult",
        type=float,
        default=2.0,
        help="Sizing stop distance = mult * ATR(14) (not a live stop).",
    )
    parser.add_argument("--time-stop-days", type=int, default=10)
    parser.add_argument(
        "--financing-bps-per-day",
        type=float,
        default=0.0,
        help="Optional overnight debit in bps of notional per day (from IG product page).",
    )
    parser.add_argument("--starting-cash", type=float, default=50_000.0)
    parser.add_argument(
        "--is-end",
        default="2015-12-31",
        help="In-sample end date (inclusive). OOS starts the next day.",
    )
    parser.add_argument("--oos-start", default="2016-01-01")
    parser.add_argument(
        "--csv",
        type=Path,
        default=Path("data/research/connors_rsi2.csv"),
    )
    parser.add_argument("--no-discord", action="store_true")
    args = parser.parse_args()

    overlays = [p.strip() for p in args.overlays.split(",") if p.strip()]
    thresholds = [float(x) for x in args.rsi_thresholds.split(",") if x.strip()]
    is_end = _parse_date(args.is_end)
    oos_start = _parse_date(args.oos_start)

    settings = Settings()
    repo = ParquetMarketDataRepository(Path(settings.data_dir), exchange="ig")

    print("Connors RSI(2) index reversal — daily bars")
    print(
        f"fill={args.fill} risk={args.risk_pct:.2%} atr_mult={args.atr_stop_mult} "
        f"time_stop={args.time_stop_days}d financing_bps/day={args.financing_bps_per_day}"
    )
    print(
        "Caveats: Yahoo cash close ≠ IG CFD daily; no pyramiding; "
        "dividends not credited; financing off unless you set it.\n"
    )

    rows: list[dict[str, Any]] = []
    for overlay in overlays:
        if overlay not in _MARKETS:
            raise SystemExit(f"Unknown overlay {overlay}; expected {sorted(_MARKETS)}")
        yahoo, epic, _ = _MARKETS[overlay]
        cfg = load_config(overlay=overlay)
        spread = float(cfg.backtest.spread_bps or 1.0)
        bars = list(repo.load_bars(epic, "1d"))
        if len(bars) < 300:
            print(
                f"# {overlay}: need daily bars — "
                f"uv run python scripts/backfill_ig_external.py "
                f"--yahoo '{yahoo}' --epic {epic} --timeframe 1d"
            )
            continue
        df = _bars_to_frame(bars)
        print(
            f"# {overlay} {epic} n={len(df)} "
            f"{df['ts'].iloc[0].date()}->{df['ts'].iloc[-1].date()} "
            f"spread_bps={spread} yahoo={yahoo}"
        )

        periods = [
            ("full", None, None),
            ("IS", None, is_end),
            ("OOS", oos_start, None),
        ]
        for thr in thresholds:
            for period_name, start, end in periods:
                slice_df = _slice_by_date(df, start, end)
                if len(slice_df) < 250:
                    print(f"  skip {period_name} rsi<{thr:g}: only {len(slice_df)} bars")
                    continue
                # Warm-up: for IS/OOS, prefer including prior bars for indicators
                # then report metrics only on the slice. Simpler: run on slice only
                # (OOS cold-starts SMA200 — slightly pessimistic).
                res = simulate(
                    slice_df,
                    rsi_threshold=thr,
                    sma_regime=200,
                    sma_exit=5,
                    time_stop_days=args.time_stop_days,
                    risk_pct=args.risk_pct,
                    atr_stop_mult=args.atr_stop_mult,
                    atr_period=14,
                    spread_bps=spread,
                    financing_bps_per_day=args.financing_bps_per_day,
                    fill=args.fill,
                    starting_cash=args.starting_cash,
                )
                row = {
                    "overlay": overlay,
                    "fill": args.fill,
                    "period": period_name,
                    "rsi_threshold": thr,
                    "n_bars": len(slice_df),
                    "spread_bps": spread,
                    "financing_bps_per_day": args.financing_bps_per_day,
                    "return_pct": res.return_pct,
                    "maxdd_pct": res.maxdd_pct,
                    "sharpe": res.sharpe,
                    "trips": res.trips,
                    "win_rate": res.win_rate,
                    "avg_hold_days": res.avg_hold_days,
                    "avg_trade_pct": res.avg_trade_pct,
                    "bh_pct": res.bh_pct,
                    "vs_bh": res.vs_bh,
                }
                rows.append(row)
                sharpe = f"{res.sharpe:.2f}" if res.sharpe is not None else "n/a"
                win = f"{res.win_rate * 100:.0f}%" if res.win_rate is not None else "n/a"
                hold = f"{res.avg_hold_days:.1f}d" if res.avg_hold_days is not None else "n/a"
                print(
                    f"  {period_name:<4} rsi<{thr:<4g} ret={res.return_pct:+7.2f}% "
                    f"vs_bh={res.vs_bh:+7.2f}% maxdd={res.maxdd_pct:6.2f}% "
                    f"sharpe={sharpe:>5} trips={res.trips:3d} win={win:>4} hold={hold}",
                    flush=True,
                )

    if not rows:
        raise SystemExit("No results — backfill daily bars first.")

    # Sensitivity summary: OOS next_open across thresholds
    print("\n=== Parameter sensitivity (OOS, same fill) ===")
    for overlay in overlays:
        subset = [
            r
            for r in rows
            if r["overlay"] == overlay and r["period"] == "OOS" and r["fill"] == args.fill
        ]
        if not subset:
            print(f"{overlay}: no OOS rows")
            continue
        rets = ", ".join(f"<{r['rsi_threshold']:g}:{r['return_pct']:+.1f}%" for r in subset)
        print(f"{overlay}: {rets}")

    args.csv.parent.mkdir(parents=True, exist_ok=True)
    with args.csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nWrote {len(rows)} rows -> {args.csv}")

    # Also run optimistic close-fill diagnostic for primary threshold=10 if default next_open
    diag_lines: list[str] = []
    if args.fill == "next_open":
        print("\n=== Diagnostic: signal_close vs next_open (rsi<10, full sample) ===")
        for overlay in overlays:
            epic = _MARKETS[overlay][1]
            bars = list(repo.load_bars(epic, "1d"))
            if len(bars) < 300:
                continue
            df = _bars_to_frame(bars)
            cfg = load_config(overlay=overlay)
            spread = float(cfg.backtest.spread_bps or 1.0)
            for fill in ("signal_close", "next_open"):
                res = simulate(
                    df,
                    rsi_threshold=10.0,
                    sma_regime=200,
                    sma_exit=5,
                    time_stop_days=args.time_stop_days,
                    risk_pct=args.risk_pct,
                    atr_stop_mult=args.atr_stop_mult,
                    atr_period=14,
                    spread_bps=spread,
                    financing_bps_per_day=args.financing_bps_per_day,
                    fill=fill,  # type: ignore[arg-type]
                    starting_cash=args.starting_cash,
                )
                line = (
                    f"{overlay} {fill}: ret={res.return_pct:+.2f}% "
                    f"vs_bh={res.vs_bh:+.2f}% trips={res.trips}"
                )
                print(f"  {line}")
                diag_lines.append(line)

    summary = "\n".join(
        [
            f"Research done: connors_rsi2 fill={args.fill}",
            f"overlays={','.join(overlays)} thresholds={args.rsi_thresholds}",
            f"IS end={args.is_end} OOS start={args.oos_start}",
            "OOS results:",
            *[
                (
                    f"{r['overlay']} rsi<{r['rsi_threshold']:g} "
                    f"ret={r['return_pct']:+.2f}% vs_bh={r['vs_bh']:+.2f}% "
                    f"trips={r['trips']}"
                )
                for r in rows
                if r["period"] == "OOS"
            ],
            *(["Close vs next-open diagnostic:", *diag_lines] if diag_lines else []),
            f"csv={args.csv}",
        ]
    )
    if notify_demo_research(summary, enabled=not args.no_discord):
        print("Posted summary to Discord demo webhook.", flush=True)


if __name__ == "__main__":
    main()
