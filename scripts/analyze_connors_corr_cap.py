#!/usr/bin/env python3
"""Correlation-cap sizing: edge-weighted US split + three-way simultaneous.

1. US500+Nasdaq joint-trigger days (n≈70): flat 50/50 vs Sharpe-weighted
   split of a fixed combined risk bucket. Compare mean combined trade pickup
   vs worst joint drawdown — does weighting recover Nasdaq's edge without
   loading crash risk onto the larger leg?

2. Three-way simultaneous (all three signal same day): how often, what is
   realised combined risk under naive sum (US bucket + DAX 1%) vs a tighter
   cap (~2.2–2.5%). Does a dedicated three-way rule earn complexity?

Standalone OOS RSI<15 Sharpes (ig financing, next_open corroboration):
  US500 0.39, Nasdaq 0.61, DAX 0.38

Usage:
    uv run python scripts/analyze_connors_corr_cap.py
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from trading_platform.config.loader import load_config
from trading_platform.config.settings import Settings
from trading_platform.indicators.atr import compute_atr
from trading_platform.indicators.rsi import compute_rsi
from trading_platform.indicators.sma import compute_sma
from trading_platform.market_data.repository.parquet import ParquetMarketDataRepository

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_connors_overlay_risk import _bars_to_frame, _corr, _max_drawdown  # noqa: E402
from analyze_connors_planning_inputs import _connors_signal_mask  # noqa: E402
from research_connors_rsi2 import _MARKETS, _ig_long_financing_bps_per_night  # noqa: E402

OVERLAYS = ["ig-us500", "ig-us-tech100", "ig-dax-daily"]
US = "ig-us500"
NDX = "ig-us-tech100"
DAX = "ig-dax-daily"
OOS_START = date(2016, 1, 1)
VOL_CAP = 1.5
ATR_STOP_MULT = 2.0
TIME_STOP = 10
RSI_THR = 15.0
STARTING_CASH = 50_000.0

# Locked standalone OOS Sharpes (corroboration, rsi<15, ig financing).
SHARPE = {US: 0.395, NDX: 0.608, DAX: 0.383}


@dataclass
class MarketSeries:
    overlay: str
    df: pd.DataFrame  # ts, open, high, low, close + computed cols
    spread_bps: float
    fin_bps: float


def _prep_market(overlay: str, repo: ParquetMarketDataRepository) -> MarketSeries:
    _, epic, currency = _MARKETS[overlay]
    bars = list(repo.load_bars(epic, "1d"))
    df = _bars_to_frame(bars)
    df = df[df["ts"].dt.date >= OOS_START].reset_index(drop=True)
    close = df["close"]
    df["rsi"] = compute_rsi(close, period=2)
    df["sma200"] = compute_sma(close, period=200)
    df["sma5"] = compute_sma(close, period=5)
    df["atr"] = compute_atr(df["high"], df["low"], close, period=14)
    ret = close.pct_change().fillna(0.0)
    rv_fast = ret.rolling(10, min_periods=10).std()
    rv_slow = rv_fast.rolling(60, min_periods=60).mean()
    df["vol_ratio"] = rv_fast / rv_slow
    df["ret"] = ret
    df["signal"] = _connors_signal_mask(df, rsi_threshold=RSI_THR)
    df["date"] = pd.to_datetime(df["ts"]).dt.normalize()
    spread = float(load_config(overlay=overlay).backtest.spread_bps or 1.0)
    fin = _ig_long_financing_bps_per_night(currency)
    return MarketSeries(overlay=overlay, df=df, spread_bps=spread, fin_bps=fin)


def _find_exit_i(df: pd.DataFrame, entry_i: int) -> int:
    """Connors tilt exit index (signal bar); fill is next open / last close."""
    close = df["close"].to_numpy(dtype=np.float64)
    sma200 = df["sma200"].to_numpy(dtype=np.float64)
    sma5 = df["sma5"].to_numpy(dtype=np.float64)
    n = len(df)
    for i in range(entry_i, n):
        if not (sma200[i] == sma200[i] and sma5[i] == sma5[i]):
            continue
        held = i - entry_i
        if close[i] < sma200[i] or close[i] > sma5[i] or held >= TIME_STOP:
            return i
    return n - 1


def _simulate_trade(
    m: MarketSeries,
    signal_i: int,
    *,
    risk_pct: float,
    equity: float = STARTING_CASH,
) -> dict[str, Any] | None:
    """Enter next open after signal_i if vol throttle allows; return trade stats."""
    df = m.df
    fill_i = signal_i + 1
    if fill_i >= len(df):
        return None
    vr = float(df["vol_ratio"].iloc[fill_i])
    if vr == vr and vr >= VOL_CAP:
        return {"skipped_vol": True, "signal_date": df["date"].iloc[signal_i].date().isoformat()}
    atr = float(df["atr"].iloc[fill_i])
    px_in = float(df["open"].iloc[fill_i])
    if atr <= 0 or px_in <= 0 or atr != atr:
        return None
    qty = (equity * risk_pct) / (ATR_STOP_MULT * atr)
    if qty <= 0:
        return None
    notional_in = qty * px_in
    spread_in = notional_in * (m.spread_bps / 10_000.0)
    cash = -(notional_in + spread_in)

    exit_sig_i = _find_exit_i(df, fill_i)
    exit_fill_i = min(exit_sig_i + 1, len(df) - 1)
    # If exit signal on last bar, flatten at close
    if exit_sig_i >= len(df) - 1:
        exit_fill_i = len(df) - 1
        px_out = float(df["close"].iloc[exit_fill_i])
    else:
        px_out = float(df["open"].iloc[exit_fill_i])

    # Financing across nights while held (fill_i .. exit_fill_i-1 inclusive nights)
    fin_cost = 0.0
    for i in range(fill_i + 1, exit_fill_i + 1):
        prev = pd.Timestamp(df["ts"].iloc[i - 1]).date()
        cur = pd.Timestamp(df["ts"].iloc[i]).date()
        nights = max(1, (cur - prev).days)
        fin_cost += qty * float(df["open"].iloc[i]) * (m.fin_bps / 10_000.0) * nights

    notional_out = qty * px_out
    spread_out = notional_out * (m.spread_bps / 10_000.0)
    cash += notional_out - spread_out - fin_cost
    pnl = cash
    pnl_pct = pnl / equity * 100.0

    # Daily MTM % of equity while open (close marks / exit fill on last bar)
    mtm_pct: list[float] = []
    peak = 0.0
    max_dd = 0.0
    running_fin = 0.0
    for i in range(fill_i, exit_fill_i + 1):
        if i > fill_i:
            prev = pd.Timestamp(df["ts"].iloc[i - 1]).date()
            cur = pd.Timestamp(df["ts"].iloc[i]).date()
            nights = max(1, (cur - prev).days)
            running_fin += qty * float(df["open"].iloc[i]) * (m.fin_bps / 10_000.0) * nights
        mark_px = float(df["close"].iloc[i]) if i < exit_fill_i else px_out
        # Position value - entry cost - accrued fin - entry spread (exit spread at end)
        pos_val = qty * mark_px
        pnl_now = pos_val - notional_in - spread_in - running_fin
        if i == exit_fill_i:
            pnl_now -= spread_out
        pct = pnl_now / equity * 100.0
        mtm_pct.append(pct)
        if pct > peak:
            peak = pct
        dd = pct - peak
        if dd < max_dd:
            max_dd = dd

    return {
        "skipped_vol": False,
        "signal_date": df["date"].iloc[signal_i].date().isoformat(),
        "entry_date": df["date"].iloc[fill_i].date().isoformat(),
        "exit_date": df["date"].iloc[exit_fill_i].date().isoformat(),
        "entry_i": fill_i,
        "exit_i": exit_fill_i,
        "risk_pct": risk_pct,
        "pnl_pct": pnl_pct,
        "max_dd_pct": max_dd,
        "hold_days": int(exit_fill_i - fill_i),
        "entry_vol": vr if vr == vr else None,
        "mtm_by_date": {
            df["date"].iloc[i].date().isoformat(): mtm_pct[j]
            for j, i in enumerate(range(fill_i, exit_fill_i + 1))
        },
    }


def _eff_combined(w1: float, w2: float, rho: float) -> float:
    return float(np.sqrt(w1**2 + w2**2 + 2.0 * rho * w1 * w2))


def _eff_three(w: dict[str, float], corr: dict[tuple[str, str], float]) -> float:
    keys = list(w)
    var = 0.0
    for a in keys:
        var += w[a] ** 2
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            rho = corr[(a, b)] if (a, b) in corr else corr[(b, a)]
            var += 2.0 * rho * w[a] * w[b]
    return float(np.sqrt(var))


def part1_edge_weighted(markets: dict[str, MarketSeries]) -> list[dict[str, Any]]:
    print("=" * 72)
    print("1. Edge-weighted vs flat 50/50 on US500+Nasdaq joint-trigger days")
    print("=" * 72)

    us, ndx = markets[US], markets[NDX]
    # Align dates
    us_by = us.df.set_index("date")
    ndx_by = ndx.df.set_index("date")
    common = us_by.index.intersection(ndx_by.index)
    both = us_by.loc[common, "signal"].fillna(False) & ndx_by.loc[common, "signal"].fillna(False)
    joint_dates = list(common[both])
    print(f"Joint signal days (calendar align): n={len(joint_dates)}")
    print(f"Sharpes: US500={SHARPE[US]:.3f}  Nasdaq={SHARPE[NDX]:.3f}")

    # ρ on joint days (return corr)
    sub = pd.DataFrame(
        {
            "u": us_by.loc[joint_dates, "ret"],
            "n": ndx_by.loc[joint_dates, "ret"],
        }
    ).dropna()
    rho = _corr(sub["u"].to_numpy(), sub["n"].to_numpy()) or 0.88
    print(f"Return ρ on joint-signal days: {rho:+.3f} (n={len(sub)})\n")

    s_us, s_ndx = SHARPE[US], SHARPE[NDX]
    w_us = s_us / (s_us + s_ndx)
    w_ndx = s_ndx / (s_us + s_ndx)
    print(f"Weighted split of bucket: US500 {w_us:.1%} / Nasdaq {w_ndx:.1%}")
    print("Flat split of bucket:     US500 50.0% / Nasdaq 50.0%\n")

    buckets = [0.015, 0.020]  # 1.5% and 2.0% combined sum-of-legs
    rows: list[dict[str, Any]] = []
    pair_rows: list[dict[str, Any]] = []

    # Map date -> signal index in each market frame
    us_date_to_i = {d: i for i, d in enumerate(us.df["date"])}
    ndx_date_to_i = {d: i for i, d in enumerate(ndx.df["date"])}

    for bucket in buckets:
        schemes = {
            "flat_50_50": (bucket * 0.5, bucket * 0.5),
            "sharpe_weighted": (bucket * w_us, bucket * w_ndx),
        }
        print(f"--- Combined bucket sum-of-legs = {bucket * 100:.1f}% ---")
        for scheme, (r_us, r_ndx) in schemes.items():
            eff = _eff_combined(r_us, r_ndx, rho)
            combined_pnls: list[float] = []
            combined_dds: list[float] = []
            n_both_filled = 0
            n_partial = 0
            n_skip = 0
            # Chronological equity of joint pairs only (unit stake each pair)
            eq = 100.0
            eq_path = [eq]
            worst_pair = 0.0
            worst_pair_date = ""

            for d in joint_dates:
                i_us = us_date_to_i.get(d)
                i_ndx = ndx_date_to_i.get(d)
                if i_us is None or i_ndx is None:
                    continue
                t_us = _simulate_trade(us, i_us, risk_pct=r_us)
                t_ndx = _simulate_trade(ndx, i_ndx, risk_pct=r_ndx)
                if t_us is None or t_ndx is None:
                    continue
                if t_us.get("skipped_vol") or t_ndx.get("skipped_vol"):
                    # Count only if at least one skipped entirely
                    if t_us.get("skipped_vol") and t_ndx.get("skipped_vol"):
                        n_skip += 1
                        continue
                    n_partial += 1
                    # Size the one that filled; other contributes 0
                    pnl = 0.0
                    mtm_dates: dict[str, float] = {}
                    for t, filled in (
                        (t_us, not t_us.get("skipped_vol")),
                        (t_ndx, not t_ndx.get("skipped_vol")),
                    ):
                        if filled:
                            pnl += float(t["pnl_pct"])
                            for k, v in t["mtm_by_date"].items():
                                mtm_dates[k] = mtm_dates.get(k, 0.0) + v
                    # still record
                else:
                    n_both_filled += 1
                    pnl = float(t_us["pnl_pct"]) + float(t_ndx["pnl_pct"])
                    mtm_dates = {}
                    for t in (t_us, t_ndx):
                        for k, v in t["mtm_by_date"].items():
                            mtm_dates[k] = mtm_dates.get(k, 0.0) + v

                combined_pnls.append(pnl)
                # Overlapping MTM path max DD for this pair
                if mtm_dates:
                    series = [mtm_dates[k] for k in sorted(mtm_dates)]
                    peak = series[0]
                    dd = 0.0
                    for x in series:
                        if x > peak:
                            peak = x
                        dd = min(dd, x - peak)
                    combined_dds.append(dd)
                else:
                    combined_dds.append(0.0)

                if pnl < worst_pair:
                    worst_pair = pnl
                    worst_pair_date = str(pd.Timestamp(d).date())

                eq *= 1.0 + pnl / 100.0
                eq_path.append(eq)

                pair_rows.append(
                    {
                        "bucket_pct": bucket * 100,
                        "scheme": scheme,
                        "signal_date": str(pd.Timestamp(d).date()),
                        "risk_us_pct": r_us * 100,
                        "risk_ndx_pct": r_ndx * 100,
                        "combined_pnl_pct": pnl,
                        "pair_mtm_maxdd_pct": combined_dds[-1],
                        "both_filled": int(
                            not (t_us.get("skipped_vol") or t_ndx.get("skipped_vol"))
                        ),
                    }
                )

            mean_pnl = float(np.mean(combined_pnls)) if combined_pnls else float("nan")
            median_pnl = float(np.median(combined_pnls)) if combined_pnls else float("nan")
            worst_pnl = float(np.min(combined_pnls)) if combined_pnls else float("nan")
            mean_dd = float(np.mean(combined_dds)) if combined_dds else float("nan")
            worst_dd = float(np.min(combined_dds)) if combined_dds else float("nan")
            path_dd = _max_drawdown(np.asarray(eq_path, dtype=np.float64))
            total_pnl = float(np.sum(combined_pnls)) if combined_pnls else 0.0

            print(
                f"  {scheme:18s}  legs {r_us * 100:.2f}%+{r_ndx * 100:.2f}%  "
                f"eff≈{eff * 100:.2f}%  "
                f"n_both={n_both_filled} partial={n_partial} skip={n_skip}  "
                f"mean_pnl {mean_pnl:+.3f}%  median {median_pnl:+.3f}%  "
                f"worst_pair {worst_pnl:+.3f}% ({worst_pair_date})  "
                f"mean_mtm_dd {mean_dd:+.3f}%  worst_mtm_dd {worst_dd:+.3f}%  "
                f"chain_dd {path_dd:+.2f}%  sum_pnl {total_pnl:+.2f}%"
            )
            rows.append(
                {
                    "bucket_pct": bucket * 100,
                    "scheme": scheme,
                    "risk_us_pct": r_us * 100,
                    "risk_ndx_pct": r_ndx * 100,
                    "eff_combined_pct": eff * 100,
                    "rho": rho,
                    "n_joint_days": len(joint_dates),
                    "n_both_filled": n_both_filled,
                    "n_partial": n_partial,
                    "n_both_skipped": n_skip,
                    "mean_combined_pnl_pct": mean_pnl,
                    "median_combined_pnl_pct": median_pnl,
                    "worst_combined_pnl_pct": worst_pnl,
                    "worst_pair_date": worst_pair_date,
                    "mean_pair_mtm_dd_pct": mean_dd,
                    "worst_pair_mtm_dd_pct": worst_dd,
                    "joint_chain_maxdd_pct": path_dd,
                    "sum_combined_pnl_pct": total_pnl,
                }
            )

        # Head-to-head delta at this bucket
        flat = next(
            r for r in rows if r["bucket_pct"] == bucket * 100 and r["scheme"] == "flat_50_50"
        )
        wtd = next(
            r for r in rows if r["bucket_pct"] == bucket * 100 and r["scheme"] == "sharpe_weighted"
        )
        print(
            f"  Δ weighted−flat:  mean_pnl {wtd['mean_combined_pnl_pct'] - flat['mean_combined_pnl_pct']:+.3f}pp  "
            f"worst_pair {wtd['worst_combined_pnl_pct'] - flat['worst_combined_pnl_pct']:+.3f}pp  "
            f"worst_mtm_dd {wtd['worst_pair_mtm_dd_pct'] - flat['worst_pair_mtm_dd_pct']:+.3f}pp  "
            f"chain_dd {wtd['joint_chain_maxdd_pct'] - flat['joint_chain_maxdd_pct']:+.2f}pp  "
            f"sum_pnl {wtd['sum_combined_pnl_pct'] - flat['sum_combined_pnl_pct']:+.2f}pp\n"
        )

    out_pairs = Path("data/research/connors_corr_cap_joint_pairs.csv")
    with out_pairs.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(pair_rows[0].keys()))
        w.writeheader()
        w.writerows(pair_rows)
    print(f"Wrote {out_pairs}\n")
    return rows


def part2_three_way(markets: dict[str, MarketSeries]) -> list[dict[str, Any]]:
    print("=" * 72)
    print("2. Three-way simultaneous cap")
    print("=" * 72)

    # Panel
    frames = []
    for o in OVERLAYS:
        f = markets[o].df[["date", "ret", "signal"]].copy()
        f = f.rename(columns={"ret": f"ret_{o}", "signal": f"sig_{o}"}).set_index("date")
        frames.append(f)
    panel = frames[0].join(frames[1:], how="outer").sort_index()
    for o in OVERLAYS:
        panel[f"sig_{o}"] = panel[f"sig_{o}"].fillna(False)

    both_us = panel[f"sig_{US}"] & panel[f"sig_{NDX}"]
    all3 = both_us & panel[f"sig_{DAX}"]
    any_sig = panel[[f"sig_{o}" for o in OVERLAYS]].any(axis=1)

    n_oos = len(panel.dropna(subset=[f"ret_{US}", f"ret_{NDX}", f"ret_{DAX}"], how="all"))
    print(f"OOS panel days≈{n_oos}")
    print(f"  any-signal days:           {int(any_sig.sum())}")
    print(f"  US500+Nasdaq both:         {int(both_us.sum())}")
    print(f"  all three simultaneous:    {int(all3.sum())}")
    if int(all3.sum()) > 0:
        dates = [str(pd.Timestamp(d).date()) for d in panel.index[all3][:20]]
        print(f"  three-way dates (first 20): {dates}")
    print()

    # Conditional corrs on three-way days (fall back to both-signal / any if tiny n)
    def pair_rho(a: str, b: str, mask: pd.Series, label: str) -> tuple[float, int, str]:
        sub = panel.loc[mask, [f"ret_{a}", f"ret_{b}"]].dropna()
        used = label
        c = _corr(sub[f"ret_{a}"].to_numpy(), sub[f"ret_{b}"].to_numpy()) if len(sub) >= 5 else None
        if c is None:
            # _corr needs n≥20; broaden to pairwise both-signal / either
            if a != DAX and b != DAX:
                sub = panel.loc[both_us, [f"ret_{a}", f"ret_{b}"]].dropna()
                used = "both_us_fallback"
            else:
                both_ab = panel[f"sig_{a}"] & panel[f"sig_{b}"]
                sub = panel.loc[both_ab, [f"ret_{a}", f"ret_{b}"]].dropna()
                used = "both_pair_fallback"
                if len(sub) < 20:
                    either = panel[f"sig_{a}"] | panel[f"sig_{b}"]
                    sub = panel.loc[either, [f"ret_{a}", f"ret_{b}"]].dropna()
                    used = "either_pair_fallback"
            c = _corr(sub[f"ret_{a}"].to_numpy(), sub[f"ret_{b}"].to_numpy())
        return (c if c is not None else 0.0, len(sub), used)

    mask3 = all3
    rho_us_ndx, n1, u1 = pair_rho(US, NDX, mask3, "three_way")
    rho_us_dax, n2, u2 = pair_rho(US, DAX, mask3, "three_way")
    rho_ndx_dax, n3, u3 = pair_rho(NDX, DAX, mask3, "three_way")
    # Prefer both-signal pairwise corrs for DAX (more stable) when three-way n tiny
    rho_us_ndx_b, n1b, _ = pair_rho(US, NDX, both_us, "both_us")
    both_ud = panel[f"sig_{US}"] & panel[f"sig_{DAX}"]
    both_nd = panel[f"sig_{NDX}"] & panel[f"sig_{DAX}"]
    rho_us_dax_b, n2b, _ = pair_rho(US, DAX, both_ud, "both_us_dax")
    rho_ndx_dax_b, n3b, _ = pair_rho(NDX, DAX, both_nd, "both_ndx_dax")

    print("Correlations used for effective-risk sketch:")
    print(
        f"  US×Nasdaq  three-way n={n1} ρ={rho_us_ndx:+.3f} ({u1}); both-us n={n1b} ρ={rho_us_ndx_b:+.3f}"
    )
    print(
        f"  US×DAX     three-way n={n2} ρ={rho_us_dax:+.3f} ({u2}); both n={n2b} ρ={rho_us_dax_b:+.3f}"
    )
    print(
        f"  Ndx×DAX    three-way n={n3} ρ={rho_ndx_dax:+.3f} ({u3}); both n={n3b} ρ={rho_ndx_dax_b:+.3f}"
    )
    print()

    # Use stable pairwise both-signal corrs for sizing math
    corr = {
        (US, NDX): rho_us_ndx_b,
        (US, DAX): rho_us_dax_b,
        (NDX, DAX): rho_ndx_dax_b,
    }

    rows: list[dict[str, Any]] = []
    # Caps to compare
    # Naive: US bucket 1.5% or 2.0% (flat legs) + DAX 1.0%
    # Tighter: total 2.2% or 2.5% scaled pro-rata from naive
    scenarios: list[tuple[str, dict[str, float]]] = []
    for us_bucket in (0.015, 0.020):
        naive = {
            US: us_bucket * 0.5,
            NDX: us_bucket * 0.5,
            DAX: 0.01,
        }
        scenarios.append((f"naive_us{us_bucket * 100:.1f}_dax1.0", naive))
        naive_sum = sum(naive.values())
        for total_cap in (0.022, 0.025):
            if total_cap >= naive_sum - 1e-12:
                # Cap not binding
                continue
            scale = total_cap / naive_sum
            capped = {k: v * scale for k, v in naive.items()}
            scenarios.append((f"cap{total_cap * 100:.1f}_from_us{us_bucket * 100:.1f}", capped))

    print("Effective combined risk (pairwise both-signal ρ) vs naive sum-of-legs:")
    for name, w in scenarios:
        eff = _eff_three(w, corr)
        s = sum(w.values())
        print(
            f"  {name:32s}  legs "
            f"US{w[US] * 100:.2f}+Ndx{w[NDX] * 100:.2f}+DAX{w[DAX] * 100:.2f}={s * 100:.2f}%  "
            f"eff≈{eff * 100:.2f}%  (sum−eff = {(s - eff) * 100:.2f}pp diversification credit)"
        )
        rows.append(
            {
                "scenario": name,
                "risk_us_pct": w[US] * 100,
                "risk_ndx_pct": w[NDX] * 100,
                "risk_dax_pct": w[DAX] * 100,
                "sum_legs_pct": s * 100,
                "eff_combined_pct": eff * 100,
                "diversification_credit_pp": (s - eff) * 100,
                "rho_us_ndx": corr[(US, NDX)],
                "rho_us_dax": corr[(US, DAX)],
                "rho_ndx_dax": corr[(NDX, DAX)],
                "n_three_way_days": int(all3.sum()),
            }
        )
    print()

    # Realised worst-day combined return on three-way days under each sizing
    # Proxy: day-of-signal return * risk weight (1-day shock), and also full trade PnL sum
    print("Realised three-way days — 1-day shock proxy (signal-day ret × risk_pct):")
    three_dates = list(panel.index[all3])
    date_to_i = {o: {d: i for i, d in enumerate(markets[o].df["date"])} for o in OVERLAYS}

    shock_rows: list[dict[str, Any]] = []
    if not three_dates:
        print("  (no three-way days — cannot stress realised path; rely on eff-risk sketch)")
    else:
        for name, w in scenarios:
            shocks: list[float] = []
            trade_sums: list[float] = []
            for d in three_dates:
                shock = 0.0
                trade_sum = 0.0
                all_ok = True
                for o in OVERLAYS:
                    if d not in date_to_i[o]:
                        all_ok = False
                        break
                    i = date_to_i[o][d]
                    ret = float(markets[o].df["ret"].iloc[i])
                    shock += w[o] * ret * 100.0  # risk_pct * daily ret as %-of-equity proxy
                    t = _simulate_trade(markets[o], i, risk_pct=w[o])
                    if t is None or t.get("skipped_vol"):
                        all_ok = False
                        break
                    trade_sum += float(t["pnl_pct"])
                if not all_ok:
                    continue
                shocks.append(shock)
                trade_sums.append(trade_sum)
                shock_rows.append(
                    {
                        "scenario": name,
                        "signal_date": str(pd.Timestamp(d).date()),
                        "shock_pct": shock,
                        "combined_trade_pnl_pct": trade_sum,
                    }
                )
            if shocks:
                print(
                    f"  {name:32s}  n={len(shocks)}  "
                    f"mean_shock {np.mean(shocks):+.3f}%  worst_shock {np.min(shocks):+.3f}%  "
                    f"mean_trade_pnl {np.mean(trade_sums):+.3f}%  "
                    f"worst_trade_pnl {np.min(trade_sums):+.3f}%"
                )
            else:
                print(f"  {name:32s}  n=0 filled (vol throttle skipped all)")

            # Attach aggregates
            for r in rows:
                if r["scenario"] == name:
                    r["n_three_way_filled"] = len(shocks)
                    r["mean_shock_pct"] = float(np.mean(shocks)) if shocks else None
                    r["worst_shock_pct"] = float(np.min(shocks)) if shocks else None
                    r["mean_trade_pnl_pct"] = float(np.mean(trade_sums)) if trade_sums else None
                    r["worst_trade_pnl_pct"] = float(np.min(trade_sums)) if trade_sums else None

    # Compare naive vs tightest binding cap
    print(
        "\n  Interpretation:\n"
        "  - If eff combined under naive is already ≪ sum-of-legs, diversification\n"
        "    credit is doing the work a tighter cap would do — skip third rule.\n"
        "  - If worst three-way shock/trade under naive ≈ under 2.2–2.5% cap, the\n"
        "    tighter rule does not earn complexity on realised stress.\n"
    )

    if shock_rows:
        out_s = Path("data/research/connors_corr_cap_threeway_days.csv")
        with out_s.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(shock_rows[0].keys()))
            w.writeheader()
            w.writerows(shock_rows)
        print(f"Wrote {out_s}")

    rows.append(
        {
            "scenario": "_meta_counts",
            "n_three_way_days": int(all3.sum()),
            "n_us_pair_days": int(both_us.sum()),
            "n_any_signal_days": int(any_sig.sum()),
        }
    )
    return rows


def main() -> None:
    settings = Settings()
    repo = ParquetMarketDataRepository(Path(settings.data_dir), exchange="ig")
    markets = {o: _prep_market(o, repo) for o in OVERLAYS}

    joint_rows = part1_edge_weighted(markets)
    three_rows = part2_three_way(markets)

    out1 = Path("data/research/connors_corr_cap_splits.csv")
    out2 = Path("data/research/connors_corr_cap_threeway.csv")
    out1.parent.mkdir(parents=True, exist_ok=True)
    with out1.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(joint_rows[0].keys()))
        w.writeheader()
        w.writerows(joint_rows)
    keys: list[str] = []
    for r in three_rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with out2.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(three_rows)
    print(f"Wrote {out1}")
    print(f"Wrote {out2}")

    # Verdict stubs printed from data
    print("\n=== Verdict draft ===")
    for bucket in (1.5, 2.0):
        flat = next(
            r for r in joint_rows if r["bucket_pct"] == bucket and r["scheme"] == "flat_50_50"
        )
        wtd = next(
            r for r in joint_rows if r["bucket_pct"] == bucket and r["scheme"] == "sharpe_weighted"
        )
        dp = wtd["mean_combined_pnl_pct"] - flat["mean_combined_pnl_pct"]
        dworst = wtd["worst_combined_pnl_pct"] - flat["worst_combined_pnl_pct"]
        ddd = wtd["worst_pair_mtm_dd_pct"] - flat["worst_pair_mtm_dd_pct"]
        print(
            f"  bucket {bucket}%: weighted Δmean_pnl {dp:+.3f}pp  "
            f"Δworst_pair {dworst:+.3f}pp  Δworst_mtm_dd {ddd:+.3f}pp"
        )


if __name__ == "__main__":
    main()
