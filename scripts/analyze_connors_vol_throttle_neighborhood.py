#!/usr/bin/env python3
"""Vol-throttle neighborhood + 2022 interaction + per-market sensitivity."""

from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

from trading_platform.config.loader import load_config
from trading_platform.config.settings import Settings
from trading_platform.market_data.repository.parquet import ParquetMarketDataRepository

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_connors_overlay_risk import (  # noqa: E402
    _WINDOWS,
    _bars_to_frame,
    _window_stats,
    simulate_paths,
)
from research_connors_rsi2 import _MARKETS, _ig_long_financing_bps_per_night  # noqa: E402

OVERLAYS = ["ig-us500", "ig-us-tech100", "ig-dax-daily"]
RATIOS = [1.25, 1.5, 1.75, 2.0]
OOS_START = date(2016, 1, 1)
COVID = _WINDOWS["covid_crash"]
BEAR = _WINDOWS["bear_2022"]


def main() -> None:
    settings = Settings()
    repo = ParquetMarketDataRepository(Path(settings.data_dir), exchange="ig")
    rows: list[dict] = []

    print("Vol-throttle neighborhood + 2022 interaction")
    print("skip new tilt when rv10/mean(rv10,60) >= ratio; core+tilt otherwise unchanged\n")

    for overlay in OVERLAYS:
        _yahoo, epic, currency = _MARKETS[overlay]
        fin = _ig_long_financing_bps_per_night(currency)
        cfg = load_config(overlay=overlay)
        spread = float(cfg.backtest.spread_bps or 1.0)
        df = _bars_to_frame(list(repo.load_bars(epic, "1d")))
        df = df[df["ts"].dt.date >= OOS_START].reset_index(drop=True)

        base_path, base_sum = simulate_paths(
            df,
            rsi_threshold=15.0,
            risk_pct=0.01,
            atr_stop_mult=2.0,
            time_stop_days=10,
            spread_bps=spread,
            financing_bps_per_night=fin,
            starting_cash=50_000.0,
            tilt_risk_pct=0.01,
        )
        base_pickup = base_sum["overlay_ret_pct"] - base_sum["bh_ret_pct"]
        base_covid = float(_window_stats(base_path, *COVID)["overlay_vs_bh_maxdd_pp"])
        base_bear = float(_window_stats(base_path, *BEAR)["overlay_vs_bh_maxdd_pp"])
        base_bear_st = _window_stats(base_path, *BEAR)
        base_bear_pickup = float(base_bear_st["overlay_ret_pct"]) - float(
            base_bear_st["bh_ret_pct"]
        )

        print(
            f"# {overlay}  baseline pickup={base_pickup:+.1f}pp  "
            f"covid_amp={base_covid:+.2f}  bear_amp={base_bear:+.2f}  "
            f"bear_pickup={base_bear_pickup:+.1f}pp"
        )

        for ratio in [None, *RATIOS]:
            label = "baseline" if ratio is None else f"vol_{ratio:g}x"
            path, summary = simulate_paths(
                df,
                rsi_threshold=15.0,
                risk_pct=0.01,
                atr_stop_mult=2.0,
                time_stop_days=10,
                spread_bps=spread,
                financing_bps_per_night=fin,
                starting_cash=50_000.0,
                tilt_risk_pct=0.01,
                vol_ratio_cap=ratio,
            )
            covid_st = _window_stats(path, *COVID)
            bear_st = _window_stats(path, *BEAR)
            pickup = summary["overlay_ret_pct"] - summary["bh_ret_pct"]
            covid_amp = float(covid_st["overlay_vs_bh_maxdd_pp"])
            bear_amp = float(bear_st["overlay_vs_bh_maxdd_pp"])
            bear_pickup = float(bear_st["overlay_ret_pct"]) - float(bear_st["bh_ret_pct"])

            bear_mask = (path["ts"].dt.date >= BEAR[0]) & (path["ts"].dt.date <= BEAR[1])
            covid_mask = (path["ts"].dt.date >= COVID[0]) & (path["ts"].dt.date <= COVID[1])
            base_bear_mask = (base_path["ts"].dt.date >= BEAR[0]) & (
                base_path["ts"].dt.date <= BEAR[1]
            )
            base_covid_mask = (base_path["ts"].dt.date >= COVID[0]) & (
                base_path["ts"].dt.date <= COVID[1]
            )

            tilt_days_bear = int(path.loc[bear_mask, "tilt_in_pos"].sum())
            tilt_days_covid = int(path.loc[covid_mask, "tilt_in_pos"].sum())
            base_tilt_bear = int(base_path.loc[base_bear_mask, "tilt_in_pos"].sum())
            base_tilt_covid = int(base_path.loc[base_covid_mask, "tilt_in_pos"].sum())

            vr = path["vol_ratio"]
            if ratio is not None:
                elev_bear = int(((vr >= ratio) & bear_mask).sum())
                elev_covid = int(((vr >= ratio) & covid_mask).sum())
                elev_oos = int((vr >= ratio).sum())
            else:
                elev_bear = elev_covid = elev_oos = 0

            rows.append(
                {
                    "overlay": overlay,
                    "variant": label,
                    "ratio": ratio if ratio is not None else "",
                    "oos_pickup_pp": pickup,
                    "oos_pickup_vs_baseline_pp": pickup - base_pickup,
                    "covid_amp_pp": covid_amp,
                    "covid_amp_recovered_pp": base_covid - covid_amp,
                    "bear_amp_pp": bear_amp,
                    "bear_amp_recovered_pp": base_bear - bear_amp,
                    "bear_pickup_pp": bear_pickup,
                    "bear_pickup_vs_baseline_pp": bear_pickup - base_bear_pickup,
                    "tilt_entries": summary["tilt_entries"],
                    "tilt_vol_skips": summary["tilt_vol_skips"],
                    "tilt_days_covid": tilt_days_covid,
                    "tilt_days_bear": tilt_days_bear,
                    "tilt_days_covid_vs_base": tilt_days_covid - base_tilt_covid,
                    "tilt_days_bear_vs_base": tilt_days_bear - base_tilt_bear,
                    "elevated_days_oos": elev_oos,
                    "elevated_days_covid": elev_covid,
                    "elevated_days_bear": elev_bear,
                }
            )

            if ratio is None:
                continue
            print(
                f"  {label:12s}  pickup {pickup:+.1f}pp (Δ{pickup - base_pickup:+.1f})  "
                f"covid amp {covid_amp:+.2f} (rec {base_covid - covid_amp:+.2f})  "
                f"bear amp {bear_amp:+.2f} (rec {base_bear - bear_amp:+.2f})  "
                f"bear pickup {bear_pickup:+.1f}pp "
                f"(Δ{bear_pickup - base_bear_pickup:+.1f})  "
                f"skips={summary['tilt_vol_skips']}  "
                f"elev_days covid/bear/oos={elev_covid}/{elev_bear}/{elev_oos}  "
                f"tilt_days covid/bear Δ="
                f"{tilt_days_covid - base_tilt_covid:+d}/{tilt_days_bear - base_tilt_bear:+d}"
            )
        print()

    out = Path("data/research/connors_vol_throttle_neighborhood.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {out}")

    print("\n=== Neighborhood scorecard (mean across 3 markets) ===")
    print(
        f"{'ratio':>8} {'covid_rec':>10} {'Δpickup':>10} {'bear_rec':>10} "
        f"{'Δbear_pick':>11} {'still_amp':>10} {'mean_skips':>11}"
    )
    for ratio in RATIOS:
        subset = [r for r in rows if r["variant"] == f"vol_{ratio:g}x"]
        n = len(subset)
        covid_rec = sum(r["covid_amp_recovered_pp"] for r in subset) / n
        dpick = sum(r["oos_pickup_vs_baseline_pp"] for r in subset) / n
        bear_rec = sum(r["bear_amp_recovered_pp"] for r in subset) / n
        dbear = sum(r["bear_pickup_vs_baseline_pp"] for r in subset) / n
        still = sum(1 for r in subset if r["covid_amp_pp"] > 0.25)
        skips = sum(r["tilt_vol_skips"] for r in subset) / n
        print(
            f"{ratio:8.2f} {covid_rec:+10.2f} {dpick:+10.1f} {bear_rec:+10.2f} "
            f"{dbear:+11.1f} {still:6d}/{n:<3d} {skips:11.1f}"
        )

    print("\n=== Per-market covid amp by ratio ===")
    print(f"{'ratio':>8}", end="")
    for o in OVERLAYS:
        print(f"  {o:>16}", end="")
    print()
    for ratio in [None, *RATIOS]:
        label = "base" if ratio is None else f"{ratio:g}"
        print(f"{label:>8}", end="")
        for o in OVERLAYS:
            if ratio is None:
                r = next(x for x in rows if x["overlay"] == o and x["variant"] == "baseline")
            else:
                r = next(x for x in rows if x["overlay"] == o and x["variant"] == f"vol_{ratio:g}x")
            print(f"  {r['covid_amp_pp']:+16.2f}", end="")
        print()

    print("\n=== Per-market OOS Δpickup vs baseline ===")
    print(f"{'ratio':>8}", end="")
    for o in OVERLAYS:
        print(f"  {o:>16}", end="")
    print()
    for ratio in RATIOS:
        print(f"{ratio:8.2f}", end="")
        for o in OVERLAYS:
            r = next(x for x in rows if x["overlay"] == o and x["variant"] == f"vol_{ratio:g}x")
            print(f"  {r['oos_pickup_vs_baseline_pp']:+16.1f}", end="")
        print()

    print("\n=== Per-market 2022 Δbear_pickup / elev_days / tilt_days Δ ===")
    for o in OVERLAYS:
        print(f"  {o}:")
        for ratio in RATIOS:
            r = next(x for x in rows if x["overlay"] == o and x["variant"] == f"vol_{ratio:g}x")
            print(
                f"    {ratio:g}x  Δbear_pick={r['bear_pickup_vs_baseline_pp']:+.2f}pp  "
                f"bear_amp_rec={r['bear_amp_recovered_pp']:+.2f}  "
                f"elev_bear={r['elevated_days_bear']}  "
                f"tilt_days_bear_Δ={r['tilt_days_bear_vs_base']:+d}  "
                f"skips_total={r['tilt_vol_skips']}"
            )

    print("\n=== Stability around 1.5× ===")
    for o in OVERLAYS:
        recs = {}
        picks = {}
        for ratio in RATIOS:
            r = next(x for x in rows if x["overlay"] == o and x["variant"] == f"vol_{ratio:g}x")
            recs[ratio] = r["covid_amp_recovered_pp"]
            picks[ratio] = r["oos_pickup_vs_baseline_pp"]
        print(
            f"  {o}: covid_rec @1.25/1.5/1.75/2.0 = "
            f"{recs[1.25]:+.2f}/{recs[1.5]:+.2f}/{recs[1.75]:+.2f}/{recs[2.0]:+.2f}  "
            f"Δpickup = {picks[1.25]:+.1f}/{picks[1.5]:+.1f}/"
            f"{picks[1.75]:+.1f}/{picks[2.0]:+.1f}"
        )

    # Verdict helpers
    print("\n=== Robustness verdict ===")
    # Smoothness: covid recovery at 1.25 and 1.75 within 1pp of 1.5 mean?
    mean_rec = {
        ratio: sum(r["covid_amp_recovered_pp"] for r in rows if r["variant"] == f"vol_{ratio:g}x")
        / 3
        for ratio in RATIOS
    }
    mean_pick = {
        ratio: sum(
            r["oos_pickup_vs_baseline_pp"] for r in rows if r["variant"] == f"vol_{ratio:g}x"
        )
        / 3
        for ratio in RATIOS
    }
    mean_bear_pick = {
        ratio: sum(
            r["bear_pickup_vs_baseline_pp"] for r in rows if r["variant"] == f"vol_{ratio:g}x"
        )
        / 3
        for ratio in RATIOS
    }
    print("  mean covid_rec: " + ", ".join(f"{r:g}×={mean_rec[r]:+.2f}" for r in RATIOS))
    print("  mean Δpickup:   " + ", ".join(f"{r:g}×={mean_pick[r]:+.1f}" for r in RATIOS))
    print("  mean Δ2022 pick:" + ", ".join(f"{r:g}×={mean_bear_pick[r]:+.2f}" for r in RATIOS))

    # DAX-specific: does 1.5 uniquely matter?
    dax = {
        ratio: next(
            r for r in rows if r["overlay"] == "ig-dax-daily" and r["variant"] == f"vol_{ratio:g}x"
        )
        for ratio in RATIOS
    }
    print("  DAX covid amp: " + ", ".join(f"{r:g}×={dax[r]['covid_amp_pp']:+.2f}" for r in RATIOS))
    if (
        abs(dax[1.5]["covid_amp_pp"] - dax[1.25]["covid_amp_pp"]) < 0.5
        and abs(dax[1.5]["covid_amp_pp"] - dax[1.75]["covid_amp_pp"]) < 1.0
    ):
        print("  DAX: 1.5× sits in a smooth neighborhood — not a knife-edge unique to 1.5.")
    elif dax[1.5]["covid_amp_pp"] < dax[1.75]["covid_amp_pp"] - 1.0:
        print(
            "  DAX: 1.5× clearly better than 1.75/2.0 — market may want a tighter "
            "default than US; consider per-market threshold or stick to ≤1.5."
        )
    else:
        print("  DAX: inspect neighborhood manually — mixed pattern.")

    if all(abs(mean_bear_pick[r]) < 1.0 for r in RATIOS):
        print(
            "  2022: throttle does not materially cut bear-window pickup "
            "(|Δ| < 1pp across ratios) — no bad interaction with SMA200 bear."
        )
    else:
        print("  2022: some ratios cut bear pickup — check per-market Δbear_pick above.")


if __name__ == "__main__":
    main()
