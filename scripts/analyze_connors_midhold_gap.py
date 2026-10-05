#!/usr/bin/env python3
"""Mid-hold gap check: calm tilt entry → sharp vol spike while open.

Mar 2020 never stresses this — entry throttle zeros tilt days there. Feb 2018
(Volmageddon) and Dec 2018 are the historical shapes where entry can look
fine and the regime flips mid-hold.

Compares (entry throttle locked at 1.5):
  B  bare SMA200 exit (current Connors)
  D  SMA200∧vol≥1.5 exit (candidate mid-hold rule)
  F  no SMA200 exit (SMA5 / time only)
  G  force-close on mid-hold vol≥1.5 alone (diagnostic: does the gap need
     a vol trigger without waiting for SMA200?)

Usage:
    uv run python scripts/analyze_connors_midhold_gap.py
"""

from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from trading_platform.config.loader import load_config
from trading_platform.config.settings import Settings
from trading_platform.indicators.atr import compute_atr
from trading_platform.indicators.rsi import compute_rsi
from trading_platform.indicators.sma import compute_sma
from trading_platform.market_data.repository.parquet import ParquetMarketDataRepository

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_connors_overlay_risk import (  # noqa: E402
    _bars_to_frame,
    _max_drawdown,
    _signal_flags,
    _window_stats,
)
from research_connors_rsi2 import _MARKETS, _ig_long_financing_bps_per_night  # noqa: E402

ExitMode = Literal["always", "with_vol", "never", "vol_only"]

OVERLAYS = ["ig-us500", "ig-us-tech100", "ig-dax-daily"]
OOS_START = date(2016, 1, 1)
VOL_CAP = 1.5

# Stress windows shaped for calm→spike (not already-elevated like Mar 2020).
_WINDOWS: dict[str, tuple[date, date]] = {
    # Late-Jan peak → Volmageddon trough / VIX spike (5–8 Feb), short recovery.
    "volmageddon": (date(2018, 1, 26), date(2018, 2, 16)),
    # Q4 2018 orderly decline → late-Dec waterfall → early-Jan bounce.
    "dec_2018": (date(2018, 11, 1), date(2019, 1, 4)),
}


def simulate_overlay(
    df: pd.DataFrame,
    *,
    spread_bps: float,
    financing_bps_per_night: float,
    starting_cash: float = 50_000.0,
    tilt_risk_pct: float = 0.01,
    atr_stop_mult: float = 2.0,
    rsi_threshold: float = 15.0,
    time_stop_days: int = 10,
    vol_ratio_cap: float | None = VOL_CAP,
    sma200_exit: ExitMode = "always",
    vol_exit_cap: float = VOL_CAP,
) -> tuple[pd.DataFrame, dict[str, Any], list[dict[str, Any]]]:
    """Core+tilt with trade log for mid-hold gap diagnostics."""
    close = df["close"]
    rsi = compute_rsi(close, period=2)
    sma200 = compute_sma(close, period=200)
    sma5 = compute_sma(close, period=5)
    atr = compute_atr(df["high"], df["low"], close, period=14)

    open_ = df["open"].to_numpy(dtype=np.float64)
    close_a = close.to_numpy(dtype=np.float64)
    rsi_a = rsi.to_numpy(dtype=np.float64)
    sma200_a = sma200.to_numpy(dtype=np.float64)
    sma5_a = sma5.to_numpy(dtype=np.float64)
    atr_a = atr.to_numpy(dtype=np.float64)
    ts = pd.to_datetime(df["ts"]).to_numpy()
    n = len(df)

    mkt_ret = np.zeros(n)
    mkt_ret[1:] = close_a[1:] / close_a[:-1] - 1.0
    ret_s = pd.Series(mkt_ret)
    rv_fast = ret_s.rolling(10, min_periods=10).std()
    rv_slow = rv_fast.rolling(60, min_periods=60).mean()
    vol_ratio = (rv_fast / rv_slow).to_numpy(dtype=np.float64)

    core_shares = starting_cash / close_a[0]
    t_qty = 0.0
    t_entry_i = -1
    t_pend_in = False
    t_pend_out = False
    t_pend_reason = ""
    o_cash = 0.0
    o_eq: list[float] = []
    tilt_in: list[int] = []
    entries = 0
    vol_skips = 0
    exit_counts = {"sma200": 0, "sma200_vol": 0, "sma5": 0, "time": 0, "vol_midhold": 0}
    trades: list[dict[str, Any]] = []

    # Live trade scratch
    tr_entry_vol: float | None = None
    tr_entry_px: float | None = None
    tr_max_vol: float = float("-inf")
    tr_min_close_vs_sma: float = float("inf")  # close/sma200 - 1
    tr_days_below200 = 0
    tr_days_vol_hot = 0

    def o_mark(px: float) -> float:
        return o_cash + (core_shares + t_qty) * px

    def _reset_trade_scratch() -> None:
        nonlocal tr_entry_vol, tr_entry_px, tr_max_vol, tr_min_close_vs_sma
        nonlocal tr_days_below200, tr_days_vol_hot
        tr_entry_vol = None
        tr_entry_px = None
        tr_max_vol = float("-inf")
        tr_min_close_vs_sma = float("inf")
        tr_days_below200 = 0
        tr_days_vol_hot = 0

    def t_enter(i: int, px: float) -> None:
        nonlocal t_qty, t_entry_i, o_cash, entries, vol_skips
        nonlocal tr_entry_vol, tr_entry_px, tr_max_vol
        if t_qty > 0 or px <= 0 or atr_a[i] != atr_a[i] or atr_a[i] <= 0:
            return
        if vol_ratio_cap is not None:
            ratio = vol_ratio[i]
            if ratio == ratio and ratio >= vol_ratio_cap:
                vol_skips += 1
                return
        eq = o_mark(px)
        raw = (eq * tilt_risk_pct) / (atr_stop_mult * atr_a[i])
        if raw <= 0:
            return
        notional = raw * px
        spread = notional * (spread_bps / 10_000.0)
        o_cash -= notional + spread
        t_qty = raw
        t_entry_i = i
        entries += 1
        vr = vol_ratio[i]
        tr_entry_vol = float(vr) if vr == vr else None
        tr_entry_px = float(px)
        tr_max_vol = float(vr) if vr == vr else float("-inf")

    def t_exit(i: int, px: float, reason: str) -> None:
        nonlocal t_qty, t_entry_i, o_cash
        if t_qty <= 0 or px <= 0:
            return
        notional = t_qty * px
        spread = notional * (spread_bps / 10_000.0)
        o_cash += notional - spread
        pnl_pct = (
            (px / tr_entry_px - 1.0) * 100.0 if tr_entry_px and tr_entry_px > 0 else float("nan")
        )
        trades.append(
            {
                "entry_date": pd.Timestamp(ts[t_entry_i]).date().isoformat(),
                "exit_date": pd.Timestamp(ts[i]).date().isoformat(),
                "hold_days": int(i - t_entry_i),
                "exit_reason": reason,
                "entry_vol": tr_entry_vol,
                "max_vol_held": tr_max_vol if tr_max_vol != float("-inf") else None,
                "days_below_sma200": tr_days_below200,
                "days_vol_hot": tr_days_vol_hot,
                "min_close_vs_sma200_pct": (
                    tr_min_close_vs_sma * 100.0 if tr_min_close_vs_sma != float("inf") else None
                ),
                "pnl_pct": pnl_pct,
                "calm_entry": (tr_entry_vol is not None and tr_entry_vol < VOL_CAP),
                "vol_spiked_midhold": (
                    tr_entry_vol is not None and tr_entry_vol < VOL_CAP and tr_max_vol >= VOL_CAP
                ),
            }
        )
        t_qty = 0.0
        t_entry_i = -1
        _reset_trade_scratch()

    for i in range(n):
        if i > 0 and financing_bps_per_night > 0 and t_qty > 0:
            prev = pd.Timestamp(ts[i - 1]).date()
            cur = pd.Timestamp(ts[i]).date()
            nights = max(1, (cur - prev).days)
            o_cash -= t_qty * open_[i] * (financing_bps_per_night / 10_000.0) * nights

        if t_pend_out:
            t_exit(i, open_[i], t_pend_reason)
            t_pend_out = False
            t_pend_reason = ""
        if t_pend_in and t_qty == 0.0:
            t_enter(i, open_[i])
            t_pend_in = False

        o_eq.append(o_mark(close_a[i]))
        tilt_in.append(1 if t_qty > 0 else 0)

        # Update live trade scratch at close
        if t_qty > 0:
            vr = vol_ratio[i]
            if vr == vr:
                tr_max_vol = max(tr_max_vol, float(vr))
                if vr >= VOL_CAP:
                    tr_days_vol_hot += 1
            if sma200_a[i] == sma200_a[i] and sma200_a[i] > 0:
                vs = close_a[i] / sma200_a[i] - 1.0
                tr_min_close_vs_sma = min(tr_min_close_vs_sma, vs)
                if close_a[i] < sma200_a[i]:
                    tr_days_below200 += 1

        prev_rsi = rsi_a[i - 1] if i > 0 else float("nan")
        t_in, _ = _signal_flags(
            in_position=t_qty > 0,
            entry_i=t_entry_i,
            i=i,
            close=close_a[i],
            rsi=rsi_a[i],
            prev_rsi=prev_rsi,
            sma200=sma200_a[i],
            sma5=sma5_a[i],
            rsi_threshold=rsi_threshold,
            time_stop_days=time_stop_days,
        )

        t_out = False
        exit_reason = ""
        if (
            t_qty > 0
            and rsi_a[i] == rsi_a[i]
            and sma200_a[i] == sma200_a[i]
            and sma5_a[i] == sma5_a[i]
        ):
            held = i - t_entry_i
            below200 = close_a[i] < sma200_a[i]
            above5 = close_a[i] > sma5_a[i]
            timed = held >= time_stop_days
            vr = vol_ratio[i]
            vol_hot = vr == vr and vr >= vol_exit_cap

            if sma200_exit == "vol_only":
                # Diagnostic: mid-hold vol spike alone forces exit (before SMA5)
                if vol_hot:
                    t_out = True
                    exit_reason = "vol_midhold"
                elif above5 or timed:
                    t_out = True
                    exit_reason = "sma5" if above5 else "time"
            elif above5 or timed:
                t_out = True
                exit_reason = "sma5" if above5 else "time"
            elif below200:
                if sma200_exit == "always":
                    t_out = True
                    exit_reason = "sma200"
                elif sma200_exit == "with_vol" and vol_hot:
                    t_out = True
                    exit_reason = "sma200_vol"
                # never / with_vol cold: hold
            # never: ignore SMA200

        if t_out and t_qty > 0 and not t_pend_out:
            t_pend_out = True
            t_pend_in = False
            t_pend_reason = exit_reason
            if exit_reason in exit_counts:
                exit_counts[exit_reason] += 1
        elif t_in and t_qty == 0.0:
            t_pend_in = True

    if t_qty > 0:
        t_exit(n - 1, close_a[-1], "eod")
        o_eq[-1] = o_mark(close_a[-1])

    o_arr = np.asarray(o_eq, dtype=np.float64)
    bh_eq = starting_cash * (close_a / close_a[0])
    path = pd.DataFrame(
        {
            "ts": pd.to_datetime(df["ts"]),
            "close": close_a,
            "mkt_ret": mkt_ret,
            "vol_ratio": vol_ratio,
            "tilt_in_pos": np.asarray(tilt_in, dtype=np.int8),
            "below_sma200": (close_a < sma200_a).astype(np.int8),
            "bh_eq": bh_eq,
            "overlay_eq": o_arr,
            "in_pos": np.asarray(tilt_in, dtype=np.int8),
            "connors_eq": o_arr,
        }
    )
    summary = {
        "overlay_ret_pct": (o_arr[-1] / starting_cash - 1.0) * 100.0,
        "overlay_maxdd_pct": _max_drawdown(o_arr),
        "bh_ret_pct": (bh_eq[-1] / starting_cash - 1.0) * 100.0,
        "bh_maxdd_pct": _max_drawdown(bh_eq),
        "tilt_entries": entries,
        "tilt_vol_skips": vol_skips,
        "sma200_exits": exit_counts["sma200"],
        "sma200_vol_exits": exit_counts["sma200_vol"],
        "sma5_exits": exit_counts["sma5"],
        "vol_midhold_exits": exit_counts["vol_midhold"],
        "tilt_time_pct": 100.0 * float(np.mean(tilt_in)),
    }
    return path, summary, trades


def _trade_in_window(tr: dict[str, Any], start: date, end: date) -> bool:
    """Trade overlaps window if any hold day intersects [start, end]."""
    e0 = date.fromisoformat(tr["entry_date"])
    e1 = date.fromisoformat(tr["exit_date"])
    return e0 <= end and e1 >= start


def main() -> None:
    variants: list[tuple[str, ExitMode]] = [
        ("B_entry_throttle_bare_sma200", "always"),
        ("D_entry_throttle_sma200_and_vol", "with_vol"),
        ("F_entry_throttle_no_sma200", "never"),
        ("G_entry_throttle_vol_midhold", "vol_only"),
    ]

    settings = Settings()
    repo = ParquetMarketDataRepository(Path(settings.data_dir), exchange="ig")
    rows: list[dict[str, Any]] = []
    gap_trades: list[dict[str, Any]] = []

    print("=" * 72)
    print("Mid-hold gap: calm entry → vol spike while open (Feb / Dec 2018)")
    print("=" * 72)
    print(
        "Entry throttle locked at 1.5. Question: does bare SMA200 handle the\n"
        "failure mode Mar 2020 couldn't stress? If not, does SMA200∧vol help,\n"
        "or is a mid-hold vol-only exit required?\n"
    )

    for overlay in OVERLAYS:
        _, epic, currency = _MARKETS[overlay]
        fin = _ig_long_financing_bps_per_night(currency)
        spread = float(load_config(overlay=overlay).backtest.spread_bps or 1.0)
        df = _bars_to_frame(list(repo.load_bars(epic, "1d")))
        df = df[df["ts"].dt.date >= OOS_START].reset_index(drop=True)

        print(f"# {overlay}")
        base_pickup = None
        base_amps: dict[str, float] = {}

        for vname, mode in variants:
            path, summary, trades = simulate_overlay(
                df,
                spread_bps=spread,
                financing_bps_per_night=fin,
                sma200_exit=mode,
            )
            pickup = summary["overlay_ret_pct"] - summary["bh_ret_pct"]
            if vname.startswith("B_"):
                base_pickup = pickup

            # OOS gap-shaped trades under this rule
            gap_all = [t for t in trades if t["vol_spiked_midhold"]]
            for wname, (ws, we) in _WINDOWS.items():
                wst = _window_stats(path, ws, we)
                amp = float(wst["overlay_vs_bh_maxdd_pp"])
                if vname.startswith("B_"):
                    base_amps[wname] = amp
                rec = (base_amps[wname] - amp) if wname in base_amps else 0.0

                mask = (path["ts"].dt.date >= ws) & (path["ts"].dt.date <= we)
                tilt_d = int(path.loc[mask, "tilt_in_pos"].sum())
                max_vr = float(path.loc[mask, "vol_ratio"].max())
                min_vr = float(path.loc[mask, "vol_ratio"].min())
                days_below = int(path.loc[mask, "below_sma200"].sum())

                win_trades = [t for t in trades if _trade_in_window(t, ws, we)]
                gap_win = [t for t in win_trades if t["vol_spiked_midhold"]]
                calm_win = [t for t in win_trades if t["calm_entry"]]

                print(
                    f"  {vname:36s} {wname:12s}  "
                    f"amp {amp:+.2f} (rec {rec:+.2f})  "
                    f"tilt_d={tilt_d}  max_vr={max_vr:.2f}  "
                    f"days_below200={days_below}  "
                    f"trades={len(win_trades)} calm={len(calm_win)} "
                    f"gap={len(gap_win)}  "
                    f"exits sma200/sma200∨vol/vol/sma5="
                    f"{summary['sma200_exits']}/{summary['sma200_vol_exits']}/"
                    f"{summary['vol_midhold_exits']}/{summary['sma5_exits']}"
                )

                rows.append(
                    {
                        "overlay": overlay,
                        "variant": vname,
                        "window": wname,
                        "amp_pp": amp,
                        "amp_recovered_vs_B_pp": rec,
                        "oos_pickup_pp": pickup,
                        "oos_pickup_vs_B_pp": (
                            (pickup - base_pickup) if base_pickup is not None else 0.0
                        ),
                        "tilt_days": tilt_d,
                        "max_vol_ratio": max_vr,
                        "min_vol_ratio": min_vr,
                        "days_below_sma200": days_below,
                        "window_trades": len(win_trades),
                        "calm_entry_trades": len(calm_win),
                        "gap_trades": len(gap_win),
                        "gap_mean_pnl_pct": (
                            float(np.mean([t["pnl_pct"] for t in gap_win])) if gap_win else None
                        ),
                        "sma200_exits": summary["sma200_exits"],
                        "sma200_vol_exits": summary["sma200_vol_exits"],
                        "vol_midhold_exits": summary["vol_midhold_exits"],
                        "sma5_exits": summary["sma5_exits"],
                    }
                )

                if vname.startswith("B_"):
                    for t in gap_win:
                        gap_trades.append(
                            {
                                "overlay": overlay,
                                "window": wname,
                                "variant": vname,
                                **t,
                            }
                        )

            print(
                f"  {'':36s} OOS pickup {pickup:+.1f}pp "
                f"(Δvs B {(pickup - base_pickup) if base_pickup is not None else 0:+.1f})  "
                f"gap-shaped trades OOS={len(gap_all)}"
            )
        print()

    # Scorecard
    print("=== Window scorecard (mean amp recovered vs B, across markets) ===")
    for wname in _WINDOWS:
        print(f"  -- {wname} --")
        for vname, _ in variants:
            if vname.startswith("B_"):
                continue
            subset = [r for r in rows if r["variant"] == vname and r["window"] == wname]
            if not subset:
                continue
            n = len(subset)
            mean_rec = sum(r["amp_recovered_vs_B_pp"] for r in subset) / n
            mean_gap = sum(r["gap_trades"] for r in subset) / n
            mean_tilt = sum(r["tilt_days"] for r in subset) / n
            still = sum(1 for r in subset if r["amp_pp"] > 0.25)
            print(
                f"  {vname:36s}  mean amp_rec {mean_rec:+.2f}pp  "
                f"mean gap_trades {mean_gap:.1f}  mean tilt_d {mean_tilt:.1f}  "
                f"still amp>0.25: {still}/{n}"
            )

    print("\n=== Gap-shaped trades under B (calm entry, vol spiked mid-hold) in windows ===")
    if not gap_trades:
        print("  (none — failure mode absent in these windows under entry throttle)")
    else:
        for t in gap_trades:
            print(
                f"  {t['overlay']:16s} {t['window']:12s}  "
                f"{t['entry_date']}→{t['exit_date']}  "
                f"exit={t['exit_reason']:10s}  "
                f"entry_vr={t['entry_vol']:.2f} max_vr={t['max_vol_held']:.2f}  "
                f"days_below200={t['days_below_sma200']}  "
                f"pnl={t['pnl_pct']:+.2f}%"
            )

    print(
        "\n  Interpretation:\n"
        "  - If B shows gap trades that exit sma5/time with days_below200>0 and\n"
        "    poor pnl, bare SMA200 failed to cut them — check D recovery.\n"
        "  - If gap trades never go below SMA200 (days_below200=0), neither B nor D\n"
        "    can fire; G (vol mid-hold) is the only candidate that addresses the shape.\n"
        "  - If no gap trades at all, entry throttle + timing meant these windows\n"
        "    never opened a calm tilt into a mid-hold spike either.\n"
    )

    out1 = Path("data/research/connors_midhold_gap_windows.csv")
    out2 = Path("data/research/connors_midhold_gap_trades.csv")
    out1.parent.mkdir(parents=True, exist_ok=True)
    with out1.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        if rows:
            w.writeheader()
            w.writerows(rows)
    with out2.open("w", newline="") as f:
        fields = (
            list(gap_trades[0].keys())
            if gap_trades
            else [
                "overlay",
                "window",
                "variant",
                "entry_date",
                "exit_date",
            ]
        )
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(gap_trades)
    print(f"Wrote {out1}")
    print(f"Wrote {out2}")


if __name__ == "__main__":
    main()
