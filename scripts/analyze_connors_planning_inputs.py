#!/usr/bin/env python3
"""Two planning inputs before build:

1. Mid-hold regime exit: bare SMA200 vs SMA200∧vol≥1.5 force-close on tilt
2. Conditional correlation of daily returns on Connors tilt-signal days
   (US500 / Nasdaq / DAX) for combined risk-budget sizing

Usage:
    uv run python scripts/analyze_connors_planning_inputs.py
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
    _WINDOWS,
    _bars_to_frame,
    _corr,
    _max_drawdown,
    _signal_flags,
    _window_stats,
)
from research_connors_rsi2 import _MARKETS, _ig_long_financing_bps_per_night  # noqa: E402

Sma200ExitMode = Literal["always", "with_vol", "never"]

OVERLAYS = ["ig-us500", "ig-us-tech100", "ig-dax-daily"]
OOS_START = date(2016, 1, 1)
COVID = _WINDOWS["covid_crash"]
BEAR = _WINDOWS["bear_2022"]
VOL_CAP = 1.5


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
    vol_ratio_cap: float | None = None,
    sma200_exit: Sma200ExitMode = "always",
    vol_exit_cap: float = VOL_CAP,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Core+tilt overlay with configurable entry throttle and SMA200 exit mode.

    sma200_exit:
      always   — current Connors rule (exit tilt when close < SMA200)
      with_vol — exit on SMA200 break only if vol_ratio >= vol_exit_cap
      never    — ignore SMA200 for tilt; exit only on SMA5 / time-stop
                 (and optional with_vol force-close still available via with_vol)
    """
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
    o_cash = 0.0
    o_eq: list[float] = []
    tilt_in: list[int] = []
    entries = 0
    vol_skips = 0
    sma200_exits = 0
    sma200_vol_exits = 0
    sma5_exits = 0

    def o_mark(px: float) -> float:
        return o_cash + (core_shares + t_qty) * px

    def t_enter(i: int, px: float) -> None:
        nonlocal t_qty, t_entry_i, o_cash, entries, vol_skips
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

    def t_exit(i: int, px: float) -> None:
        nonlocal t_qty, t_entry_i, o_cash
        if t_qty <= 0 or px <= 0:
            return
        notional = t_qty * px
        spread = notional * (spread_bps / 10_000.0)
        o_cash += notional - spread
        t_qty = 0.0
        t_entry_i = -1

    for i in range(n):
        if i > 0 and financing_bps_per_night > 0 and t_qty > 0:
            prev = pd.Timestamp(ts[i - 1]).date()
            cur = pd.Timestamp(ts[i]).date()
            nights = max(1, (cur - prev).days)
            o_cash -= t_qty * open_[i] * (financing_bps_per_night / 10_000.0) * nights

        if t_pend_out:
            t_exit(i, open_[i])
            t_pend_out = False
        if t_pend_in and t_qty == 0.0:
            t_enter(i, open_[i])
            t_pend_in = False

        o_eq.append(o_mark(close_a[i]))
        tilt_in.append(1 if t_qty > 0 else 0)

        prev_rsi = rsi_a[i - 1] if i > 0 else float("nan")
        # Entry still uses full Connors (needs SMA200 bull / rsi cross)
        t_in, _t_out_unused = _signal_flags(
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

        # Rebuild tilt exit under sma200_exit mode
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

            if above5 or timed:
                t_out = True
                exit_reason = "sma5" if above5 else "time"
            elif below200:
                if sma200_exit == "always":
                    t_out = True
                    exit_reason = "sma200"
                elif sma200_exit == "with_vol" and vol_hot:
                    t_out = True
                    exit_reason = "sma200_vol"
                # else: hold through cold SMA200 break until SMA5/time
            # never: ignore bare SMA200 entirely (only sma5/time above)

        if t_out and t_qty > 0 and not t_pend_out:
            t_pend_out = True
            t_pend_in = False
            if exit_reason == "sma200":
                sma200_exits += 1
            elif exit_reason == "sma200_vol":
                sma200_vol_exits += 1
            elif exit_reason == "sma5":
                sma5_exits += 1
        elif t_in and t_qty == 0.0:
            # Entry still uses full Connors (needs SMA200 bull)
            t_pend_in = True

    if t_qty > 0:
        t_exit(n - 1, close_a[-1])
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
            # dummy for _window_stats compatibility
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
        "sma200_exits": sma200_exits,
        "sma200_vol_exits": sma200_vol_exits,
        "sma5_exits": sma5_exits,
        "tilt_time_pct": 100.0 * float(np.mean(tilt_in)),
    }
    return path, summary


def part1_midhold_exit() -> list[dict[str, Any]]:
    print("=" * 72)
    print("1. Mid-hold regime exit — bare SMA200 vs SMA200∧vol≥1.5")
    print("=" * 72)
    print(
        "Note: current Connors already exits tilt on bare SMA200 break (next open).\n"
        "Variants isolate whether that exit should require elevated vol, and whether\n"
        "entry throttle + mid-hold rule stack.\n"
    )

    variants: list[tuple[str, dict[str, Any]]] = [
        ("A_bare_sma200_no_entry_throttle", {"vol_ratio_cap": None, "sma200_exit": "always"}),
        ("B_entry_throttle_1.5_bare_sma200", {"vol_ratio_cap": VOL_CAP, "sma200_exit": "always"}),
        ("C_no_entry_throttle_sma200_and_vol", {"vol_ratio_cap": None, "sma200_exit": "with_vol"}),
        ("D_entry_throttle_and_sma200_vol", {"vol_ratio_cap": VOL_CAP, "sma200_exit": "with_vol"}),
        ("E_no_sma200_exit_at_all", {"vol_ratio_cap": None, "sma200_exit": "never"}),
        ("F_entry_throttle_no_sma200_exit", {"vol_ratio_cap": VOL_CAP, "sma200_exit": "never"}),
    ]

    settings = Settings()
    repo = ParquetMarketDataRepository(Path(settings.data_dir), exchange="ig")
    rows: list[dict[str, Any]] = []

    for overlay in OVERLAYS:
        _, epic, currency = _MARKETS[overlay]
        fin = _ig_long_financing_bps_per_night(currency)
        spread = float(load_config(overlay=overlay).backtest.spread_bps or 1.0)
        df = _bars_to_frame(list(repo.load_bars(epic, "1d")))
        df = df[df["ts"].dt.date >= OOS_START].reset_index(drop=True)

        print(f"# {overlay}")
        base_covid = None
        base_pickup = None
        for vname, kwargs in variants:
            path, summary = simulate_overlay(
                df, spread_bps=spread, financing_bps_per_night=fin, **kwargs
            )
            covid = _window_stats(path, *COVID)
            bear = _window_stats(path, *BEAR)
            pickup = summary["overlay_ret_pct"] - summary["bh_ret_pct"]
            covid_amp = float(covid["overlay_vs_bh_maxdd_pp"])
            bear_amp = float(bear["overlay_vs_bh_maxdd_pp"])
            if vname.startswith("A_"):
                base_covid = covid_amp
                base_pickup = pickup
            covid_rec = (base_covid - covid_amp) if base_covid is not None else 0.0
            dpick = (pickup - base_pickup) if base_pickup is not None else 0.0

            # tilt days in covid
            mask = (path["ts"].dt.date >= COVID[0]) & (path["ts"].dt.date <= COVID[1])
            tilt_covid = int(path.loc[mask, "tilt_in_pos"].sum())

            print(
                f"  {vname:42s}  pickup {pickup:+.1f}pp (Δ{dpick:+.1f})  "
                f"covid_amp {covid_amp:+.2f} (rec {covid_rec:+.2f})  "
                f"bear_amp {bear_amp:+.2f}  tilt_d_covid={tilt_covid}  "
                f"entries={summary['tilt_entries']} skips={summary['tilt_vol_skips']}  "
                f"exits sma200/sma200∨vol/sma5="
                f"{summary['sma200_exits']}/{summary['sma200_vol_exits']}/{summary['sma5_exits']}"
            )
            rows.append(
                {
                    "overlay": overlay,
                    "variant": vname,
                    "oos_pickup_pp": pickup,
                    "oos_pickup_vs_A_pp": dpick,
                    "covid_amp_pp": covid_amp,
                    "covid_amp_recovered_vs_A_pp": covid_rec,
                    "bear_amp_pp": bear_amp,
                    "tilt_days_covid": tilt_covid,
                    **{
                        k: summary[k]
                        for k in (
                            "tilt_entries",
                            "tilt_vol_skips",
                            "sma200_exits",
                            "sma200_vol_exits",
                            "sma5_exits",
                        )
                    },
                }
            )
        print()

    # Scorecard
    print("=== Mid-hold scorecard (mean across markets, vs A bare SMA200 no throttle) ===")
    by_v: dict[str, list] = {}
    for r in rows:
        by_v.setdefault(r["variant"], []).append(r)
    for vname, vrows in by_v.items():
        if vname.startswith("A_"):
            continue
        n = len(vrows)
        print(
            f"  {vname:42s}  mean covid_rec {sum(r['covid_amp_recovered_vs_A_pp'] for r in vrows) / n:+.2f}pp  "
            f"mean Δpickup {sum(r['oos_pickup_vs_A_pp'] for r in vrows) / n:+.1f}pp  "
            f"still covid-amp>0.25: {sum(1 for r in vrows if r['covid_amp_pp'] > 0.25)}/{n}"
        )
    print(
        "\n  Interpretation guide:\n"
        "  - B (entry throttle only) = locked design so far\n"
        "  - C (mid-hold SMA200∧vol, no entry throttle) = does mid-hold alone match B on covid?\n"
        "  - D (both) = stacking value or redundancy\n"
        "  - E/F (no SMA200 exit) = upper bound on cost of dropping regime exit\n"
    )
    return rows


def _connors_signal_mask(df: pd.DataFrame, rsi_threshold: float = 15.0) -> pd.Series:
    """Boolean mask: bar is a Connors entry signal (close)."""
    close = df["close"]
    rsi = compute_rsi(close, period=2)
    sma200 = compute_sma(close, period=200)
    n = len(df)
    sig = np.zeros(n, dtype=bool)
    rsi_a = rsi.to_numpy()
    sma_a = sma200.to_numpy()
    close_a = close.to_numpy(dtype=np.float64)
    in_pos = False
    entry_i = -1
    sma5 = compute_sma(close, period=5).to_numpy()
    for i in range(n):
        if not (rsi_a[i] == rsi_a[i] and sma_a[i] == sma_a[i] and sma5[i] == sma5[i]):
            continue
        if in_pos:
            held = i - entry_i
            if close_a[i] < sma_a[i] or close_a[i] > sma5[i] or held >= 10:
                in_pos = False
                entry_i = -1
            continue
        prev = rsi_a[i - 1] if i > 0 else float("nan")
        if (
            close_a[i] > sma_a[i]
            and rsi_a[i] < rsi_threshold
            and (prev != prev or prev >= rsi_threshold)
        ):
            sig[i] = True
            in_pos = True
            entry_i = i
    return pd.Series(sig, index=df.index)


def part2_conditional_corr() -> list[dict[str, Any]]:
    print("=" * 72)
    print("2. Conditional correlation on tilt-signal days")
    print("=" * 72)
    print(
        "Correlation of daily returns conditional on ≥1 market having a Connors\n"
        "entry signal that day (and pairwise: both of a pair signaling).\n"
    )

    settings = Settings()
    repo = ParquetMarketDataRepository(Path(settings.data_dir), exchange="ig")

    frames: dict[str, pd.DataFrame] = {}
    for overlay in OVERLAYS:
        _, epic, _ = _MARKETS[overlay]
        df = _bars_to_frame(list(repo.load_bars(epic, "1d")))
        df = df[df["ts"].dt.date >= OOS_START].reset_index(drop=True)
        df["ret"] = df["close"].pct_change()
        df["signal"] = _connors_signal_mask(df)
        df["date"] = pd.to_datetime(df["ts"]).dt.normalize()
        frames[overlay] = df[["date", "ret", "signal"]].dropna(subset=["ret"])

    # Outer-join on date
    panel = None
    for overlay, f in frames.items():
        piece = f.rename(columns={"ret": f"ret_{overlay}", "signal": f"sig_{overlay}"})
        piece = piece.set_index("date")
        panel = piece if panel is None else panel.join(piece, how="outer")

    assert panel is not None
    panel = panel.sort_index()
    # Any-signal day
    sig_cols = [f"sig_{o}" for o in OVERLAYS]
    panel["any_signal"] = panel[sig_cols].fillna(False).any(axis=1)

    pairs = [
        ("ig-us500", "ig-us-tech100"),
        ("ig-us500", "ig-dax-daily"),
        ("ig-us-tech100", "ig-dax-daily"),
    ]

    rows: list[dict[str, Any]] = []
    print(f"OOS panel days={len(panel)}  any-signal days={int(panel['any_signal'].sum())}")
    print()

    def corr_report(label: str, mask: pd.Series, a: str, b: str) -> None:
        sub = panel.loc[mask, [f"ret_{a}", f"ret_{b}"]].dropna()
        n = len(sub)
        if n < 20:
            c = None
            print(f"  {label:40s}  n={n:<5d}  corr=n/a")
        else:
            c = _corr(sub[f"ret_{a}"].to_numpy(), sub[f"ret_{b}"].to_numpy())
            print(f"  {label:40s}  n={n:<5d}  corr={c:+.3f}")
        rows.append(
            {
                "pair": f"{a}__{b}",
                "condition": label,
                "n_days": n,
                "corr": c,
            }
        )

    for a, b in pairs:
        print(f"Pair {a} × {b}")
        corr_report("unconditional (all OOS days)", panel.index.to_series().notna(), a, b)
        corr_report("any market signals", panel["any_signal"].fillna(False), a, b)
        both = panel[f"sig_{a}"].fillna(False) & panel[f"sig_{b}"].fillna(False)
        corr_report("both of pair signal same day", both, a, b)
        either = panel[f"sig_{a}"].fillna(False) | panel[f"sig_{b}"].fillna(False)
        corr_report("either of pair signals", either, a, b)
        print()

    # Suggested combined risk: if US500-Nasdaq corr ≈ ρ on both-signal days,
    # two 1% tilts ≈ 1% * sqrt(2+2ρ) in variance terms for equal weights
    print("=== Combined risk budget sketch (equal 1% tilt legs) ===")
    for a, b in pairs:
        both = panel[f"sig_{a}"].fillna(False) & panel[f"sig_{b}"].fillna(False)
        sub = panel.loc[both, [f"ret_{a}", f"ret_{b}"]].dropna()
        if len(sub) < 10:
            # fall back to any-signal
            any_m = panel["any_signal"].fillna(False)
            sub = panel.loc[any_m, [f"ret_{a}", f"ret_{b}"]].dropna()
            used = "any_signal"
        else:
            used = "both_signal"
        if len(sub) < 20:
            print(f"  {a}+{b}: insufficient overlap")
            continue
        rho = _corr(sub[f"ret_{a}"].to_numpy(), sub[f"ret_{b}"].to_numpy())
        assert rho is not None
        # Variance of sum of two unit risks with corr ρ: 2 + 2ρ; effective risk vs one unit
        eff = (2.0 + 2.0 * rho) ** 0.5  # in units of single-leg risk
        # To keep combined ≈ 1.5× single-leg risk budget:
        # scale each leg so scale * eff = 1.5 → scale = 1.5/eff
        # Or: max combined budget 2% with two legs → each gets 2%/eff
        single = 0.01
        combined_if_independent = single * (2**0.5)
        combined_at_rho = single * eff
        scale_for_1p5 = 0.015 / eff  # each leg risk_pct to hit 1.5% combined
        scale_for_2p0 = 0.02 / eff
        print(
            f"  {a} + {b}  ({used}, n={len(sub)}, ρ={rho:+.3f})\n"
            f"    two×1% tilts effective combined risk ≈ {combined_at_rho * 100:.2f}% "
            f"(vs {combined_if_independent * 100:.2f}% if ρ=0, {2 * single * 100:.2f}% if ρ=1)\n"
            f"    per-leg risk_pct for ~1.5% combined budget: {scale_for_1p5 * 100:.2f}%\n"
            f"    per-leg risk_pct for ~2.0% combined budget: {scale_for_2p0 * 100:.2f}%"
        )
        rows.append(
            {
                "pair": f"{a}__{b}",
                "condition": f"budget_sketch_{used}",
                "n_days": len(sub),
                "corr": rho,
                "eff_combined_at_1pct_each": combined_at_rho,
                "per_leg_for_1p5_combined": scale_for_1p5,
                "per_leg_for_2p0_combined": scale_for_2p0,
            }
        )
    print()
    return rows


def main() -> None:
    mid_rows = part1_midhold_exit()
    corr_rows = part2_conditional_corr()

    out1 = Path("data/research/connors_midhold_exit.csv")
    out2 = Path("data/research/connors_signal_day_corr.csv")
    out1.parent.mkdir(parents=True, exist_ok=True)
    with out1.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(mid_rows[0].keys()))
        w.writeheader()
        w.writerows(mid_rows)
    # corr rows have uneven keys
    keys: list[str] = []
    for r in corr_rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with out2.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(corr_rows)
    print(f"Wrote {out1}")
    print(f"Wrote {out2}")


if __name__ == "__main__":
    main()
