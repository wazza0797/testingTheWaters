#!/usr/bin/env python3
"""IG diversified trend-following — hybrid futures/cash CFD research.

Evaluates the **book**, not single markets. Yahoo daily stand-ins under IG
epic paths (see config/research/ig-diversified-trend.yaml).

Cost model (Phase 0.5):
  - futures_cfd legs: financing=0; wider spread; periodic roll = roll_mult × spread
  - cash_cfd FX legs: tom-next financing stub; no roll
  - Circuit breaker uses directional day P&L only (excludes roll charges)

Usage:
    uv run python scripts/backfill_ig_diversified_trend.py
    uv run python scripts/research_ig_diversified_trend.py --sma 100 --risk 0.0075
    uv run python scripts/research_ig_diversified_trend.py \\
        --sma 100 --risk 0.0075 --roll-mult 1,1.5,2 --no-discord
"""

from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from trading_platform.config.settings import Settings
from trading_platform.domain.models.bar import Bar
from trading_platform.market_data.repository.parquet import ParquetMarketDataRepository
from trading_platform.notifications.research import notify_demo_research


@dataclass(frozen=True, slots=True)
class MarketSpec:
    id: str
    asset_class: str
    product: str  # futures_cfd | cash_cfd
    epic: str
    yahoo: str
    spread_bps: float
    financing_bps_per_day: float
    roll_every_bars: int
    point_value: float
    status: str


@dataclass(frozen=True, slots=True)
class PortfolioResult:
    label: str
    n_markets: int
    n_days: int
    return_pct: float
    ann_pct: float | None
    maxdd_pct: float
    sharpe: float | None
    chop_2015_pct: float | None
    crisis_2020_pct: float | None
    turnover: float
    financing_pct: float
    spread_cost_pct: float
    roll_cost_pct: float
    circuit_trips: int
    fx_risk_share: float | None
    roll_spread_mult: float


def _load_registry(path: Path) -> tuple[dict[str, Any], list[MarketSpec]]:
    with path.open(encoding="utf-8") as fh:
        reg = yaml.safe_load(fh)
    defaults = reg.get("defaults") or {}
    fx_fin_default = float(defaults.get("fx_financing_bps_per_day") or 0.5)
    markets: list[MarketSpec] = []
    for m in reg.get("markets") or []:
        product = str(m.get("product") or "cash_cfd")
        if "financing_bps_per_day" in m:
            fin = float(m["financing_bps_per_day"])
        elif product == "futures_cfd":
            fin = 0.0
        else:
            fin = fx_fin_default
        markets.append(
            MarketSpec(
                id=str(m["id"]),
                asset_class=str(m["asset_class"]),
                product=product,
                epic=str(m["epic"]),
                yahoo=str(m["yahoo"]),
                spread_bps=float(m.get("spread_bps") or 1.0),
                financing_bps_per_day=fin,
                roll_every_bars=int(m.get("roll_every_bars") or 0),
                point_value=float(m.get("point_value") or 1.0),
                status=str(m.get("status") or "provisional"),
            )
        )
    return reg, markets


def _bars_to_frame(bars: list[Bar]) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "ts": [b.timestamp for b in bars],
            "open": [float(b.open) for b in bars],
            "high": [float(b.high) for b in bars],
            "low": [float(b.low) for b in bars],
            "close": [float(b.close) for b in bars],
        }
    )
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    return frame.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)


def _load_market(
    repo: ParquetMarketDataRepository, market: MarketSpec, timeframe: str
) -> pd.DataFrame | None:
    try:
        bars = list(repo.load_bars(market.epic, timeframe))
    except FileNotFoundError:
        return None
    if len(bars) < 250:
        return None
    return _bars_to_frame(bars)


def _true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    prev = np.roll(close, 1)
    prev[0] = close[0]
    return np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))


def _atr_np(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    tr = _true_range(high, low, close)
    out = np.full_like(tr, np.nan, dtype=float)
    if len(tr) < period:
        return out
    out[period - 1] = tr[:period].mean()
    alpha = 1.0 / period
    for i in range(period, len(tr)):
        out[i] = out[i - 1] * (1 - alpha) + tr[i] * alpha
    return out


def _max_drawdown_pct(equity: np.ndarray) -> float:
    peak = equity[0]
    max_dd = 0.0
    for x in equity:
        peak = max(peak, x)
        dd = (peak - x) / peak if peak else 0.0
        max_dd = max(max_dd, dd)
    return 100.0 * max_dd


def _sharpe(daily: np.ndarray) -> float | None:
    if len(daily) < 60:
        return None
    sd = daily.std(ddof=1)
    if sd <= 1e-18:
        return None
    return float(math.sqrt(252) * daily.mean() / sd)


def _window_return(equity: pd.Series, start: str, end: str) -> float | None:
    mask = (equity.index >= start) & (equity.index < end)
    if not mask.any():
        return None
    sub = equity.loc[mask]
    if len(sub) < 2 or float(sub.iloc[0]) == 0:
        return None
    return 100.0 * (float(sub.iloc[-1]) / float(sub.iloc[0]) - 1.0)


def simulate_portfolio(
    panels: dict[str, pd.DataFrame],
    specs: dict[str, MarketSpec],
    *,
    sma_period: int,
    atr_period: int,
    risk_pct: float,
    asset_class_cap: float,
    gross_leverage_cap: float,
    circuit_breaker_day_pct: float,
    resize_every_bars: int,
    roll_spread_mult: float,
    confirm_sma: int | None,
    starting_equity: float,
    label: str,
) -> PortfolioResult:
    frames: dict[str, pd.DataFrame] = {}
    for mid, frame in panels.items():
        close = frame["close"].to_numpy(dtype=float)
        high = frame["high"].to_numpy(dtype=float)
        low = frame["low"].to_numpy(dtype=float)
        work = frame.copy()
        work["sma"] = work["close"].rolling(sma_period, min_periods=sma_period).mean()
        work["atr"] = _atr_np(high, low, close, atr_period)
        if confirm_sma is not None:
            work["sma_fast"] = work["close"].rolling(confirm_sma, min_periods=confirm_sma).mean()
        work = work.dropna(subset=["sma", "atr"]).reset_index(drop=True)
        work["date"] = work["ts"].dt.normalize()
        frames[mid] = work

    if not frames:
        raise ValueError("no market panels")

    first_dates = sorted(pd.Timestamp(f["date"].min()) for f in frames.values())
    min_markets = min(12, len(frames))
    start = first_dates[min_markets - 1]
    end = min(pd.Timestamp(f["date"].max()) for f in frames.values())
    all_dates = [pd.Timestamp(d) for d in pd.bdate_range(start, end)]
    by_date: dict[Any, dict[str, pd.Series]] = {d: {} for d in all_dates}
    for mid, f in frames.items():
        indexed = f.set_index("date").sort_index()
        indexed = indexed[~indexed.index.duplicated(keep="last")]
        filled = indexed.reindex(all_dates, method="ffill")
        for d in all_dates:
            row = filled.loc[d]
            if pd.isna(row["close"]) or pd.isna(row["atr"]) or pd.isna(row["sma"]):
                continue
            by_date[d][mid] = row

    all_dates = [d for d in all_dates if len(by_date[d]) >= min_markets]
    if len(all_dates) < 300:
        raise ValueError(f"aligned calendar too short: {len(all_dates)} days")

    equity = starting_equity
    positions: dict[str, float] = dict.fromkeys(frames, 0.0)
    bars_since_resize = 0
    bars_since_roll: dict[str, int] = dict.fromkeys(frames, 0)
    paused = False
    circuit_trips = 0
    financing_paid = 0.0
    spread_paid = 0.0
    roll_paid = 0.0
    turnover = 0.0
    fx_risk_sum = 0.0
    port_risk_sum = 0.0
    risk_obs = 0
    eq_path: list[float] = []
    ts_path: list[pd.Timestamp] = []
    daily_rets: list[float] = []

    for i in range(1, len(all_dates)):
        d_prev, d = all_dates[i - 1], all_dates[i]
        day_start_equity = equity
        rows_prev = by_date[d_prev]
        rows = by_date[d]

        # Mark-to-market.
        for mid, qty in list(positions.items()):
            if qty == 0 or mid not in rows or mid not in rows_prev:
                continue
            o = float(rows[mid]["open"])
            c = float(rows[mid]["close"])
            prev_c = float(rows_prev[mid]["close"])
            equity += qty * (o - prev_c)
            equity += qty * (c - o)

        # Per-leg overnight financing (FX cash only in hybrid registry).
        for mid, qty in positions.items():
            if qty == 0 or mid not in rows_prev:
                continue
            fin_bps = specs[mid].financing_bps_per_day
            if fin_bps <= 0:
                continue
            notion = abs(qty) * float(rows_prev[mid]["close"])
            fee = notion * (fin_bps / 10_000.0)
            equity -= fee
            financing_paid += fee

        # Futures auto-roll: guaranteed cost hit, carved out of circuit breaker.
        roll_today = 0.0
        for mid, qty in positions.items():
            every = specs[mid].roll_every_bars
            if every <= 0 or qty == 0 or mid not in rows:
                if every > 0:
                    bars_since_roll[mid] = bars_since_roll.get(mid, 0) + 1
                continue
            bars_since_roll[mid] = bars_since_roll.get(mid, 0) + 1
            if bars_since_roll[mid] < every:
                continue
            bars_since_roll[mid] = 0
            px = float(rows[mid]["close"])
            cost = abs(qty) * px * (specs[mid].spread_bps / 10_000.0) * roll_spread_mult
            equity -= cost
            roll_paid += cost
            roll_today += cost
            turnover += abs(qty) * px

        # Circuit breaker on directional day P&L only (exclude roll charges).
        equity_ex_roll = equity + roll_today
        day_ret = (
            (equity_ex_roll - day_start_equity) / day_start_equity if day_start_equity else 0.0
        )
        if day_ret <= circuit_breaker_day_pct:
            paused = True
            circuit_trips += 1
            for mid, qty in list(positions.items()):
                if qty == 0 or mid not in rows:
                    continue
                c = float(rows[mid]["close"])
                cost = abs(qty) * c * (specs[mid].spread_bps / 10_000.0)
                spread_paid += cost
                equity -= cost
                turnover += abs(qty) * c
                positions[mid] = 0.0
                bars_since_roll[mid] = 0

        # Daily gross-leverage clamp.
        if gross_leverage_cap > 0 and equity > 0:
            gross = 0.0
            for mid, qty in positions.items():
                if qty == 0 or mid not in rows:
                    continue
                gross += abs(qty) * float(rows[mid]["close"])
            max_gross = gross_leverage_cap * equity
            if gross > max_gross and gross > 0:
                lev_scale = max_gross / gross
                for mid in positions:
                    positions[mid] *= lev_scale

        bars_since_resize += 1
        do_resize = bars_since_resize >= resize_every_bars
        if do_resize:
            bars_since_resize = 0

        if not paused:
            desired_side: dict[str, int] = {}
            for mid, row in rows_prev.items():
                if mid not in frames:
                    continue
                close = float(row["close"])
                sma = float(row["sma"])
                if close > sma:
                    side = 1
                elif close < sma:
                    side = -1
                else:
                    side = 0
                if confirm_sma is not None and side != 0:
                    sma_f = float(row["sma_fast"])
                    if np.sign(close - sma_f) != side:
                        side = 0
                desired_side[mid] = side

            raw_risk: dict[str, float] = {}
            for mid, side in desired_side.items():
                if side == 0 or mid not in rows:
                    continue
                atr = float(rows_prev[mid]["atr"])
                if atr <= 0 or math.isnan(atr):
                    continue
                raw_risk[mid] = risk_pct * equity

            class_risk: dict[str, float] = {}
            for mid, risk in raw_risk.items():
                cls = specs[mid].asset_class
                class_risk[cls] = class_risk.get(cls, 0.0) + risk
            port_risk = sum(raw_risk.values()) or 1.0
            if raw_risk:
                fx_risk = sum(r for mid, r in raw_risk.items() if specs[mid].asset_class == "fx")
                fx_risk_sum += fx_risk
                port_risk_sum += port_risk
                risk_obs += 1
            scale_class = {
                cls: min(1.0, (asset_class_cap * port_risk) / r) if r > 0 else 1.0
                for cls, r in class_risk.items()
            }

            target_qty: dict[str, float] = {mid: positions[mid] for mid in frames}
            for mid, side in desired_side.items():
                if side == 0:
                    target_qty[mid] = 0.0
            for mid, risk in raw_risk.items():
                atr = float(rows_prev[mid]["atr"])
                side = desired_side[mid]
                scaled = risk * scale_class[specs[mid].asset_class]
                # Yahoo research sizes in price units; point_value is documented for
                # live IG contract mapping (esp. where cash ≠ fut, e.g. gold/oil).
                target_qty[mid] = side * (scaled / atr)

            if gross_leverage_cap > 0:
                gross = 0.0
                for mid, qty in target_qty.items():
                    if qty == 0 or mid not in rows:
                        continue
                    gross += abs(qty) * float(rows[mid]["open"])
                max_gross = gross_leverage_cap * equity
                if gross > max_gross and gross > 0:
                    lev_scale = max_gross / gross
                    for mid in target_qty:
                        target_qty[mid] *= lev_scale

            for mid in frames:
                if mid not in rows:
                    continue
                px = float(rows[mid]["open"])
                tgt = target_qty.get(mid, positions[mid])
                cur = positions[mid]
                flip = np.sign(tgt) != np.sign(cur) and (tgt != 0 or cur != 0)
                same = np.sign(tgt) == np.sign(cur) and tgt != 0 and cur != 0
                if flip or (do_resize and same) or (cur == 0 and tgt != 0):
                    delta = tgt - cur
                    if abs(delta) > 0:
                        cost = abs(delta) * px * (specs[mid].spread_bps / 10_000.0)
                        equity -= cost
                        spread_paid += cost
                        turnover += abs(delta) * px
                        positions[mid] = tgt
                        if cur == 0 and tgt != 0:
                            bars_since_roll[mid] = 0
                elif tgt == 0 and cur != 0 and mid in desired_side:
                    cost = abs(cur) * px * (specs[mid].spread_bps / 10_000.0)
                    equity -= cost
                    spread_paid += cost
                    turnover += abs(cur) * px
                    positions[mid] = 0.0
                    bars_since_roll[mid] = 0
        else:
            paused = False

        daily_rets.append(
            (equity - day_start_equity) / day_start_equity if day_start_equity else 0.0
        )
        eq_path.append(equity)
        ts_path.append(pd.Timestamp(d))

    eq = np.asarray(eq_path, dtype=float)
    net = equity / starting_equity - 1.0
    years = len(eq) / 252.0 if len(eq) else 0.0
    ann = ((1.0 + net) ** (1.0 / years) - 1.0) if years > 0 and (1.0 + net) > 0 else None
    series = pd.Series(eq_path, index=pd.DatetimeIndex(ts_path))
    fx_share = (fx_risk_sum / port_risk_sum) if port_risk_sum > 0 else None
    return PortfolioResult(
        label=label,
        n_markets=len(frames),
        n_days=len(eq),
        return_pct=100.0 * net,
        ann_pct=None if ann is None else 100.0 * ann,
        maxdd_pct=_max_drawdown_pct(eq),
        sharpe=_sharpe(np.asarray(daily_rets, dtype=float)),
        chop_2015_pct=_window_return(series, "2015-01-01", "2016-01-01"),
        crisis_2020_pct=_window_return(series, "2020-02-01", "2020-05-01"),
        turnover=turnover / starting_equity,
        financing_pct=100.0 * financing_paid / starting_equity,
        spread_cost_pct=100.0 * spread_paid / starting_equity,
        roll_cost_pct=100.0 * roll_paid / starting_equity,
        circuit_trips=circuit_trips,
        fx_risk_share=None if fx_share is None else 100.0 * fx_share,
        roll_spread_mult=roll_spread_mult,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry",
        default="config/research/ig-diversified-trend.yaml",
        type=Path,
    )
    parser.add_argument("--sma", default="100", help="Comma SMA periods")
    parser.add_argument("--risk", default="0.0075", help="Comma risk fractions of equity")
    parser.add_argument(
        "--roll-mult",
        default="",
        help="Comma roll spread multipliers (default: registry roll_spread_mult)",
    )
    parser.add_argument("--confirm", action="store_true", help="Require SMA20 confirm")
    parser.add_argument("--no-discord", action="store_true")
    parser.add_argument(
        "--out",
        default="data/research/ig_diversified_trend/summary.csv",
        type=Path,
    )
    args = parser.parse_args()

    reg, markets = _load_registry(args.registry)
    defaults = reg.get("defaults") or {}
    settings = Settings()
    repo = ParquetMarketDataRepository(Path(settings.data_dir), exchange="ig")
    timeframe = str(defaults.get("timeframe") or "1d")

    panels: dict[str, pd.DataFrame] = {}
    specs = {m.id: m for m in markets}
    missing: list[str] = []
    for m in markets:
        frame = _load_market(repo, m, timeframe)
        if frame is None:
            missing.append(m.id)
            continue
        panels[m.id] = frame
    if missing:
        print(
            f"Missing/short data for {len(missing)} markets (skipped): {', '.join(missing)}\n"
            "Run: uv run python scripts/backfill_ig_diversified_trend.py"
        )
    if len(panels) < 4:
        raise SystemExit(f"Need ≥4 markets with data; have {len(panels)}")

    n_fut = sum(1 for m in markets if m.id in panels and m.product == "futures_cfd")
    n_cash = sum(1 for m in markets if m.id in panels and m.product == "cash_cfd")
    print(
        f"Loaded {len(panels)} markets "
        f"(futures_cfd={n_fut} cash_cfd={n_cash}). "
        f"class_cap={defaults.get('asset_class_cap')}  "
        f"lev_cap={defaults.get('gross_leverage_cap')}  "
        f"circuit={defaults.get('circuit_breaker_day_pct')} "
        f"(roll costs excluded from breaker)"
    )
    print("Gate on PORTFOLIO metrics only — single-name results are diagnostic.\n")

    sma_list = [int(x) for x in args.sma.split(",") if x.strip()]
    risk_list = [float(x) for x in args.risk.split(",") if x.strip()]
    if args.roll_mult.strip():
        roll_list = [float(x) for x in args.roll_mult.split(",") if x.strip()]
    else:
        roll_list = [float(defaults.get("roll_spread_mult") or 1.0)]
    confirm = 20 if args.confirm else None
    results: list[PortfolioResult] = []

    for sma in sma_list:
        for risk in risk_list:
            for roll_m in roll_list:
                label = f"sma{sma}_risk{risk:.4f}_roll{roll_m:g}" + ("_cf20" if confirm else "")
                results.append(
                    simulate_portfolio(
                        panels,
                        specs,
                        sma_period=sma,
                        atr_period=int(defaults.get("atr_period") or 20),
                        risk_pct=risk,
                        asset_class_cap=float(defaults.get("asset_class_cap") or 0.30),
                        gross_leverage_cap=float(defaults.get("gross_leverage_cap") or 20.0),
                        circuit_breaker_day_pct=float(
                            defaults.get("circuit_breaker_day_pct") or -0.03
                        ),
                        resize_every_bars=int(defaults.get("resize_every_bars") or 5),
                        roll_spread_mult=roll_m,
                        confirm_sma=confirm,
                        starting_equity=float(defaults.get("starting_equity") or 100_000),
                        label=label,
                    )
                )

    header = (
        f"{'label':32} {'n':>2} {'ret%':>8} {'ann%':>7} {'sharpe':>7} "
        f"{'maxdd%':>7} {'2015%':>7} {'2020%':>7} {'fin%':>5} {'spr%':>5} "
        f"{'roll%':>5} {'fxR%':>5} {'brk':>3}"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r.label:32} {r.n_markets:2d} {r.return_pct:8.2f} "
            f"{(r.ann_pct if r.ann_pct is not None else float('nan')):7.2f} "
            f"{(r.sharpe if r.sharpe is not None else float('nan')):7.2f} "
            f"{r.maxdd_pct:7.2f} "
            f"{(r.chop_2015_pct if r.chop_2015_pct is not None else float('nan')):7.2f} "
            f"{(r.crisis_2020_pct if r.crisis_2020_pct is not None else float('nan')):7.2f} "
            f"{r.financing_pct:5.2f} {r.spread_cost_pct:5.2f} {r.roll_cost_pct:5.2f} "
            f"{(r.fx_risk_share if r.fx_risk_share is not None else float('nan')):5.1f} "
            f"{r.circuit_trips:3d}"
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=list(PortfolioResult.__dataclass_fields__.keys()),
        )
        writer.writeheader()
        for r in results:
            writer.writerow({k: getattr(r, k) for k in PortfolioResult.__dataclass_fields__})
    print(f"\nWrote {args.out}")

    best = max(results, key=lambda r: (r.sharpe is not None, r.sharpe or -99))
    if not args.no_discord:
        notify_demo_research(
            "**IG diversified trend (hybrid futures/cash)**\n"
            f"markets={best.n_markets} best={best.label} "
            f"ret={best.return_pct:.1f}% sharpe={best.sharpe} "
            f"maxdd={best.maxdd_pct:.1f}% roll={best.roll_cost_pct:.1f}% "
            f"fxRisk={best.fx_risk_share}"
        )


if __name__ == "__main__":
    main()
