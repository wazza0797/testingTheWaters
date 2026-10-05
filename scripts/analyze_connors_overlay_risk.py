#!/usr/bin/env python3
"""Connors RSI(2) — overlay risk and tilt-exit fix comparison.

Core+tilt overlay = always-long index + temporary Connors dip tilt.
Baseline amplifies Mar 2020 peak DD because SMA200 is too slow.

Fix comparison (default):
  - tilt hard-stop: exit tilt at entry − k·ATR (independent of SMA5 exit)
  - vol throttle: skip new tilt entries when rv10 / mean(rv10, 60) is elevated

Usage:
    uv run python scripts/analyze_connors_overlay_risk.py
    uv run python scripts/analyze_connors_overlay_risk.py --baseline-only
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from trading_platform.config.loader import load_config
from trading_platform.config.settings import Settings
from trading_platform.domain.models.bar import Bar
from trading_platform.indicators.atr import compute_atr
from trading_platform.indicators.rsi import compute_rsi
from trading_platform.indicators.sma import compute_sma
from trading_platform.market_data.repository.parquet import ParquetMarketDataRepository

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from research_connors_rsi2 import (  # noqa: E402
    _MARKETS,
    _ig_long_financing_bps_per_night,
)

# Stress windows (inclusive calendar dates).
_WINDOWS: dict[str, tuple[date, date]] = {
    "covid_crash": (date(2020, 2, 19), date(2020, 3, 23)),  # peak→trough US
    "covid_full": (date(2020, 2, 19), date(2020, 4, 30)),  # through early recovery
    "bear_2022": (date(2022, 1, 3), date(2022, 10, 12)),  # peak→trough US
}


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


def _corr(a: np.ndarray, b: np.ndarray) -> float | None:
    if len(a) < 20:
        return None
    if a.std() < 1e-18 or b.std() < 1e-18:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def _signal_flags(
    *,
    in_position: bool,
    entry_i: int,
    i: int,
    close: float,
    rsi: float,
    prev_rsi: float,
    sma200: float,
    sma5: float,
    rsi_threshold: float,
    time_stop_days: int,
) -> tuple[bool, bool]:
    """Return (want_enter, want_exit) from Connors rules at bar close."""
    if not (rsi == rsi and sma200 == sma200 and sma5 == sma5):
        return False, False
    in_bull = close > sma200
    if in_position:
        held = i - entry_i
        if close < sma200 or close > sma5 or held >= time_stop_days:
            return False, True
        return False, False
    if in_bull and rsi < rsi_threshold and (prev_rsi != prev_rsi or prev_rsi >= rsi_threshold):
        return True, False
    return False, False


def simulate_paths(
    df: pd.DataFrame,
    *,
    rsi_threshold: float,
    risk_pct: float,
    atr_stop_mult: float,
    time_stop_days: int,
    spread_bps: float,
    financing_bps_per_night: float,
    starting_cash: float,
    tilt_risk_pct: float,
    tilt_hard_stop_atr: float | None = None,
    vol_fast: int = 10,
    vol_slow: int = 60,
    vol_ratio_cap: float | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Daily paths for standalone Connors, BH, and core+tilt overlay.

    Optional tilt-only risk controls (independent of core):
      tilt_hard_stop_atr — exit tilt next open if close ≤ entry − k·ATR(entry)
      vol_ratio_cap — skip new tilt entries when rv(fast)/mean(rv,slow) ≥ cap
    """
    close = df["close"]
    high = df["high"]
    low = df["low"]
    rsi = compute_rsi(close, period=2)
    sma200 = compute_sma(close, period=200)
    sma5 = compute_sma(close, period=5)
    atr = compute_atr(high, low, close, period=14)

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
    # Rolling realized vol (std of daily rets) and its slow trailing mean.
    ret_s = pd.Series(mkt_ret)
    rv_fast = ret_s.rolling(vol_fast, min_periods=vol_fast).std()
    rv_slow_mean = rv_fast.rolling(vol_slow, min_periods=vol_slow).mean()
    vol_ratio = (rv_fast / rv_slow_mean).to_numpy(dtype=np.float64)

    # Standalone Connors
    c_cash = starting_cash
    c_qty = 0.0
    c_entry_i = -1
    c_pend_in = False
    c_pend_out = False
    c_eq: list[float] = []
    in_pos: list[int] = []
    holds: list[int] = []
    trips = 0

    # Overlay: always-long core + borrowed tilt
    core_shares = starting_cash / close_a[0] if close_a[0] > 0 else 0.0
    t_qty = 0.0
    t_entry_i = -1
    t_entry_px = 0.0
    t_entry_atr = 0.0
    t_pend_in = False
    t_pend_out = False
    o_cash = 0.0
    o_eq: list[float] = []
    below_flags: list[int] = []
    tilt_in_pos: list[int] = []
    tilt_stops = 0
    tilt_vol_skips = 0
    tilt_entries = 0

    def c_mark(px: float) -> float:
        return c_cash + c_qty * px

    def o_mark(px: float) -> float:
        return o_cash + (core_shares + t_qty) * px

    def c_enter(i: int, px: float) -> None:
        nonlocal c_cash, c_qty, c_entry_i
        if c_qty > 0 or px <= 0 or atr_a[i] != atr_a[i] or atr_a[i] <= 0:
            return
        eq = c_mark(px)
        raw = min((eq * risk_pct) / (atr_stop_mult * atr_a[i]), (eq * 0.95) / px)
        if raw <= 0:
            return
        notional = raw * px
        spread = notional * (spread_bps / 10_000.0)
        if c_cash < notional + spread:
            raw = max(0.0, (c_cash - spread) / px)
            notional = raw * px
            spread = notional * (spread_bps / 10_000.0)
        if raw <= 0:
            return
        c_cash -= notional + spread
        c_qty = raw
        c_entry_i = i

    def c_exit(i: int, px: float) -> None:
        nonlocal c_cash, c_qty, c_entry_i, trips
        if c_qty <= 0 or px <= 0:
            return
        notional = c_qty * px
        spread = notional * (spread_bps / 10_000.0)
        c_cash += notional - spread
        holds.append(i - c_entry_i)
        trips += 1
        c_qty = 0.0
        c_entry_i = -1

    def t_enter(i: int, px: float) -> None:
        nonlocal t_qty, t_entry_i, t_entry_px, t_entry_atr, o_cash, tilt_entries, tilt_vol_skips
        if t_qty > 0 or px <= 0 or atr_a[i] != atr_a[i] or atr_a[i] <= 0:
            return
        if vol_ratio_cap is not None:
            ratio = vol_ratio[i]
            if ratio == ratio and ratio >= vol_ratio_cap:
                tilt_vol_skips += 1
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
        t_entry_px = px
        t_entry_atr = float(atr_a[i])
        tilt_entries += 1

    def t_exit(i: int, px: float) -> None:
        nonlocal t_qty, t_entry_i, t_entry_px, t_entry_atr, o_cash
        if t_qty <= 0 or px <= 0:
            return
        notional = t_qty * px
        spread = notional * (spread_bps / 10_000.0)
        o_cash += notional - spread
        t_qty = 0.0
        t_entry_i = -1
        t_entry_px = 0.0
        t_entry_atr = 0.0

    for i in range(n):
        if i > 0 and financing_bps_per_night > 0:
            prev = pd.Timestamp(ts[i - 1]).date()
            cur = pd.Timestamp(ts[i]).date()
            nights = max(1, (cur - prev).days)
            unit = financing_bps_per_night / 10_000.0 * nights
            if c_qty > 0:
                c_cash -= c_qty * open_[i] * unit
            if t_qty > 0:
                o_cash -= t_qty * open_[i] * unit

        if c_pend_out:
            c_exit(i, open_[i])
            c_pend_out = False
        if c_pend_in and c_qty == 0.0:
            c_enter(i, open_[i])
            c_pend_in = False
        if t_pend_out:
            t_exit(i, open_[i])
            t_pend_out = False
        if t_pend_in and t_qty == 0.0:
            t_enter(i, open_[i])
            t_pend_in = False

        c_eq.append(c_mark(close_a[i]))
        o_eq.append(o_mark(close_a[i]))
        in_pos.append(1 if c_qty > 0 else 0)
        tilt_in_pos.append(1 if t_qty > 0 else 0)
        below_flags.append(1 if (sma200_a[i] == sma200_a[i] and close_a[i] < sma200_a[i]) else 0)

        prev_rsi = rsi_a[i - 1] if i > 0 else float("nan")
        c_in, c_out = _signal_flags(
            in_position=c_qty > 0,
            entry_i=c_entry_i,
            i=i,
            close=close_a[i],
            rsi=rsi_a[i],
            prev_rsi=prev_rsi,
            sma200=sma200_a[i],
            sma5=sma5_a[i],
            rsi_threshold=rsi_threshold,
            time_stop_days=time_stop_days,
        )
        t_in, t_out = _signal_flags(
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

        # Tilt-only hard stop (independent of SMA5 reversion exit).
        hard_stop_hit = False
        if (
            t_qty > 0
            and tilt_hard_stop_atr is not None
            and t_entry_atr > 0
            and close_a[i] <= t_entry_px - tilt_hard_stop_atr * t_entry_atr
        ):
            hard_stop_hit = True
            t_out = True

        if c_out and c_qty > 0:
            c_pend_out = True
            c_pend_in = False
        elif c_in and c_qty == 0.0:
            c_pend_in = True
        if t_out and t_qty > 0 and not t_pend_out:
            t_pend_out = True
            t_pend_in = False
            if hard_stop_hit:
                tilt_stops += 1
        elif t_in and t_qty == 0.0:
            t_pend_in = True

    if c_qty > 0:
        c_exit(n - 1, close_a[-1])
        c_eq[-1] = c_mark(close_a[-1])
    if t_qty > 0:
        t_exit(n - 1, close_a[-1])
        o_eq[-1] = o_mark(close_a[-1])

    c_arr = np.asarray(c_eq, dtype=np.float64)
    o_arr = np.asarray(o_eq, dtype=np.float64)
    bh_eq = starting_cash * (close_a / close_a[0])

    path = pd.DataFrame(
        {
            "ts": pd.to_datetime(df["ts"]),
            "close": close_a,
            "mkt_ret": mkt_ret,
            "vol_ratio": vol_ratio,
            "in_pos": np.asarray(in_pos, dtype=np.int8),
            "tilt_in_pos": np.asarray(tilt_in_pos, dtype=np.int8),
            "below_sma200": np.asarray(below_flags, dtype=np.int8),
            "connors_eq": c_arr,
            "bh_eq": bh_eq,
            "overlay_eq": o_arr,
        }
    )
    summary = {
        "trips": trips,
        "avg_hold_days": (sum(holds) / len(holds)) if holds else None,
        "time_in_market_pct": 100.0 * float(np.mean(in_pos)),
        "tilt_time_in_market_pct": 100.0 * float(np.mean(tilt_in_pos)),
        "tilt_entries": tilt_entries,
        "tilt_stops": tilt_stops,
        "tilt_vol_skips": tilt_vol_skips,
        "connors_ret_pct": (c_arr[-1] / starting_cash - 1.0) * 100.0,
        "connors_maxdd_pct": _max_drawdown(c_arr),
        "bh_ret_pct": (bh_eq[-1] / starting_cash - 1.0) * 100.0,
        "bh_maxdd_pct": _max_drawdown(bh_eq),
        "overlay_ret_pct": (o_arr[-1] / starting_cash - 1.0) * 100.0,
        "overlay_maxdd_pct": _max_drawdown(o_arr),
    }
    return path, summary


def _window_stats(path: pd.DataFrame, start: date, end: date) -> dict[str, float | int | None]:
    mask = (path["ts"].dt.date >= start) & (path["ts"].dt.date <= end)
    w = path.loc[mask].reset_index(drop=True)
    if len(w) < 5:
        return {"n_bars": len(w)}

    def _seg_ret(eq: pd.Series) -> float:
        return float(eq.iloc[-1] / eq.iloc[0] - 1.0) * 100.0

    def _seg_dd(eq: pd.Series) -> float:
        return _max_drawdown(eq.to_numpy(dtype=np.float64))

    c_rets = w["connors_eq"].pct_change().dropna().to_numpy()
    m_rets = w["mkt_ret"].iloc[1:].to_numpy()
    # align lengths
    n = min(len(c_rets), len(m_rets))
    corr = _corr(c_rets[:n], m_rets[:n]) if n else None

    in_days = int(w["in_pos"].sum())
    below_days = int(w["below_sma200"].sum())
    # Days in position while market was down that day
    both = w[(w["in_pos"] == 1) & (w["mkt_ret"] < 0)]
    tilt_on_down_days = int(len(both))

    bh_dd = _seg_dd(w["bh_eq"])
    ov_dd = _seg_dd(w["overlay_eq"])
    # maxdd values are ≤0; positive delta_abs = overlay has deeper peak loss.
    delta_abs_pp = abs(ov_dd) - abs(bh_dd)
    return {
        "n_bars": len(w),
        "days_in_pos": in_days,
        "time_in_market_pct": 100.0 * in_days / len(w),
        "days_below_sma200": below_days,
        "tilt_on_down_days": tilt_on_down_days,
        "connors_ret_pct": _seg_ret(w["connors_eq"]),
        "connors_maxdd_pct": _seg_dd(w["connors_eq"]),
        "bh_ret_pct": _seg_ret(w["bh_eq"]),
        "bh_maxdd_pct": bh_dd,
        "overlay_ret_pct": _seg_ret(w["overlay_eq"]),
        "overlay_maxdd_pct": ov_dd,
        "overlay_vs_bh_maxdd_pp": delta_abs_pp,
        "corr_connors_mkt": corr,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--overlays",
        default="ig-us500,ig-us-tech100,ig-dax-daily",
        help="Markets to analyse.",
    )
    parser.add_argument("--rsi-threshold", type=float, default=15.0)
    parser.add_argument("--risk-pct", type=float, default=0.01)
    parser.add_argument("--tilt-risk-pct", type=float, default=0.01)
    parser.add_argument("--atr-stop-mult", type=float, default=2.0)
    parser.add_argument("--time-stop-days", type=int, default=10)
    parser.add_argument("--starting-cash", type=float, default=50_000.0)
    parser.add_argument("--oos-start", default="2016-01-01")
    parser.add_argument(
        "--baseline-only",
        action="store_true",
        help="Skip fix comparison; run original per-window stress print only.",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=Path("data/research/connors_overlay_fixes.csv"),
    )
    args = parser.parse_args()
    compare = not args.baseline_only

    overlays = [p.strip() for p in args.overlays.split(",") if p.strip()]
    oos_start = date.fromisoformat(args.oos_start)
    settings = Settings()
    repo = ParquetMarketDataRepository(Path(settings.data_dir), exchange="ig")

    variants: list[tuple[str, dict[str, Any]]] = [
        ("baseline", {}),
        ("tilt_stop_1.5atr", {"tilt_hard_stop_atr": 1.5}),
        ("tilt_stop_2.0atr", {"tilt_hard_stop_atr": 2.0}),
        ("vol_throttle_1.5x", {"vol_ratio_cap": 1.5}),
        ("vol_throttle_2.0x", {"vol_ratio_cap": 2.0}),
    ]
    if not compare:
        variants = [("baseline", {})]
        args.csv = Path("data/research/connors_overlay_risk.csv")

    print(
        "Connors core+tilt overlay — fix comparison"
        if compare
        else "Connors overlay-risk diagnostic"
    )
    print(
        f"fill=next_open rsi<{args.rsi_threshold:g} tilt_risk={args.tilt_risk_pct:.2%} "
        f"financing=ig_cash_long OOS from {oos_start}"
    )
    if compare:
        print(
            "Variants: baseline | tilt hard-stop (1.5/2.0xATR from entry) | "
            "vol throttle (skip entries when rv10/mean60 >= 1.5/2.0)\n"
        )
    else:
        print("Overlay = 100% core long + ATR-risk Connors tilt.\n")

    rows: list[dict[str, Any]] = []
    covid = _WINDOWS["covid_crash"]

    for overlay in overlays:
        if overlay not in _MARKETS:
            raise SystemExit(f"Unknown overlay {overlay}")
        _yahoo, epic, currency = _MARKETS[overlay]
        fin = _ig_long_financing_bps_per_night(currency)
        cfg = load_config(overlay=overlay)
        spread = float(cfg.backtest.spread_bps or 1.0)
        bars = list(repo.load_bars(epic, "1d"))
        df = _bars_to_frame(bars)
        df = df[df["ts"].dt.date >= oos_start].reset_index(drop=True)
        if len(df) < 300:
            print(f"# {overlay}: too short ({len(df)} bars)")
            continue

        print(f"# {overlay} {epic} fin={fin:.2f}bps/night")
        baseline_pickup: float | None = None
        baseline_covid_amp: float | None = None

        for vname, vkwargs in variants:
            path, summary = simulate_paths(
                df,
                rsi_threshold=args.rsi_threshold,
                risk_pct=args.risk_pct,
                atr_stop_mult=args.atr_stop_mult,
                time_stop_days=args.time_stop_days,
                spread_bps=spread,
                financing_bps_per_night=fin,
                starting_cash=args.starting_cash,
                tilt_risk_pct=args.tilt_risk_pct,
                **vkwargs,
            )
            covid_st = _window_stats(path, covid[0], covid[1])
            bear_st = _window_stats(path, *_WINDOWS["bear_2022"])
            pickup = summary["overlay_ret_pct"] - summary["bh_ret_pct"]
            full_amp = abs(summary["overlay_maxdd_pct"]) - abs(summary["bh_maxdd_pct"])
            covid_amp = float(covid_st.get("overlay_vs_bh_maxdd_pp") or 0.0)
            bear_amp = float(bear_st.get("overlay_vs_bh_maxdd_pp") or 0.0)

            if vname == "baseline":
                baseline_pickup = pickup
                baseline_covid_amp = covid_amp

            pickup_delta = pickup - baseline_pickup if baseline_pickup is not None else 0.0
            covid_recovered = (
                (baseline_covid_amp - covid_amp) if baseline_covid_amp is not None else 0.0
            )

            extra = ""
            if vname != "baseline":
                extra = f", Δpickup {pickup_delta:+.1f}pp"
            rec = ""
            if vname != "baseline":
                rec = f" (recovered {covid_recovered:+.2f}pp)"

            print(
                f"  {vname:22s}  OOS overlay {summary['overlay_ret_pct']:+.1f}% "
                f"(pickup {pickup:+.1f}pp vs BH{extra})  "
                f"|DD|vsBH {full_amp:+.2f}pp  "
                f"covid |DD|amp {covid_amp:+.2f}pp{rec}  "
                f"bear {bear_amp:+.2f}pp  "
                f"tilt_in={summary['tilt_time_in_market_pct']:.1f}% "
                f"entries={summary['tilt_entries']} "
                f"stops={summary['tilt_stops']} vol_skips={summary['tilt_vol_skips']}"
            )

            rows.append(
                {
                    "overlay": overlay,
                    "variant": vname,
                    "oos_bh_ret_pct": summary["bh_ret_pct"],
                    "oos_overlay_ret_pct": summary["overlay_ret_pct"],
                    "oos_pickup_pp": pickup,
                    "oos_pickup_vs_baseline_pp": pickup_delta,
                    "oos_overlay_vs_bh_maxdd_pp": full_amp,
                    "covid_overlay_ret_pct": covid_st.get("overlay_ret_pct"),
                    "covid_bh_maxdd_pct": covid_st.get("bh_maxdd_pct"),
                    "covid_overlay_maxdd_pct": covid_st.get("overlay_maxdd_pct"),
                    "covid_amp_pp": covid_amp,
                    "covid_amp_recovered_pp": covid_recovered,
                    "bear_2022_amp_pp": bear_amp,
                    "tilt_time_in_market_pct": summary["tilt_time_in_market_pct"],
                    "tilt_entries": summary["tilt_entries"],
                    "tilt_stops": summary["tilt_stops"],
                    "tilt_vol_skips": summary["tilt_vol_skips"],
                }
            )
        print()

    if not rows:
        raise SystemExit("No results")
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    with args.csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows -> {args.csv}")

    if not compare:
        return

    print(
        "=== Fix scorecard (primary: covid |DD| amp recovery; secondary: OOS pickup retained) ==="
    )
    by_variant: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_variant.setdefault(str(r["variant"]), []).append(r)

    for vname, vrows in by_variant.items():
        if vname == "baseline":
            continue
        covid_rec = [float(r["covid_amp_recovered_pp"]) for r in vrows]
        pick_d = [float(r["oos_pickup_vs_baseline_pp"]) for r in vrows]
        still_amp = sum(1 for r in vrows if float(r["covid_amp_pp"]) > 0.25)
        print(
            f"  {vname:22s}  mean covid recovered {sum(covid_rec) / len(covid_rec):+.2f}pp  "
            f"mean Δpickup {sum(pick_d) / len(pick_d):+.1f}pp  "
            f"markets still amplifying covid: {still_amp}/{len(vrows)}"
        )

    stop_rows = by_variant.get("tilt_stop_1.5atr", [])
    vol_rows = by_variant.get("vol_throttle_1.5x", [])
    if stop_rows and vol_rows:
        stop_rec = sum(float(r["covid_amp_recovered_pp"]) for r in stop_rows) / len(stop_rows)
        vol_rec = sum(float(r["covid_amp_recovered_pp"]) for r in vol_rows) / len(vol_rows)
        stop_pick = sum(float(r["oos_pickup_vs_baseline_pp"]) for r in stop_rows) / len(stop_rows)
        vol_pick = sum(float(r["oos_pickup_vs_baseline_pp"]) for r in vol_rows) / len(vol_rows)
        print()
        if stop_rec >= vol_rec - 0.25 and stop_pick >= vol_pick - 1.0:
            print(
                "  Verdict: tilt hard-stop earns its complexity — recovers covid DD amp "
                "with less (or similar) OOS pickup loss vs vol throttle. Prefer stop first."
            )
        elif vol_rec > stop_rec + 0.5:
            print(
                "  Verdict: vol throttle recovers more covid DD amp — worth the extra "
                "mechanism if you accept the pickup tradeoff."
            )
        else:
            print(
                "  Verdict: mixed — neither dominates on both covid recovery and pickup; "
                "inspect per-market rows before committing."
            )


if __name__ == "__main__":
    main()
