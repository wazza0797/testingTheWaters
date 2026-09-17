#!/usr/bin/env python3
"""Session breakout research — entries in cash/FX hours, flatten before funding.

Pivot off mean-reversion: Donchian breakouts (L/S) with RegimeRouter session
gates so we do not intentionally hold into IG overnight funding.

Session windows (UTC, ignores DST; flatten hour is inclusive "from this hour"):
  ig-eurusd / FX:     entries 07–17, flatten from 21  (~before 22:00 London funding)
  ig-dax-fut:         entries 07–16, flatten from 16  (Europe day)
  ig-us500-fut:       entries 13–20, flatten from 20  (US cash hours)

Usage:
    uv run python scripts/research_ig_session_breakout.py
    uv run python scripts/research_ig_session_breakout.py --overlays ig-eurusd --timeframes 1h
    uv run python scripts/research_ig_session_breakout.py --grid medium --workers 6
"""

from __future__ import annotations

import argparse
import csv
import itertools
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from trading_platform.analytics.report import build_performance_report
from trading_platform.config.loader import StrategyConfig, load_config
from trading_platform.config.settings import Settings
from trading_platform.container import build_backtest_engine, build_container
from trading_platform.domain.models.bar import Bar
from trading_platform.market_data.instrument_rules_cache import InstrumentRulesCache
from trading_platform.market_data.repository.parquet import ParquetMarketDataRepository
from trading_platform.notifications.research import notify_demo_research

_REGIME_STRATEGY_PATH = "trading_platform.strategies.examples.regime_router:RegimeRouterStrategy"

# Overlay → (entry_start, entry_end exclusive, flatten_hour)
_SESSION: dict[str, tuple[int, int, int]] = {
    "ig-eurusd": (7, 17, 21),
    "ig-gbpusd": (7, 17, 21),
    "ig-audusd": (7, 17, 21),
    "ig-gbpeur": (7, 17, 21),
    "ig-dax": (7, 16, 16),
    "ig-dax-fut": (7, 16, 16),
    "ig-us500": (13, 20, 20),
    "ig-us500-fut": (13, 20, 20),
    "ig-ftse": (7, 16, 16),
}

STOP_GRIDS: dict[str, dict[str, float | int]] = {
    "none": {},
    "med": {
        "long_stop_atr": 1.5,
        "long_take_profit_atr": 3.0,
        "short_stop_atr": 1.5,
        "short_take_profit_atr": 3.0,
        "stop_atr_period": 14,
    },
    "wide": {
        "long_stop_atr": 2.5,
        "long_take_profit_atr": 4.0,
        "short_stop_atr": 2.5,
        "short_take_profit_atr": 4.0,
        "stop_atr_period": 14,
    },
}


@dataclass(frozen=True, slots=True)
class Case:
    family: str
    label: str
    params: dict[str, Any]


def _always_on() -> dict[str, Any]:
    return {"compare": {"indicator": "sma", "period": 1, "op": ">", "value": 0.0}}


def _adx_ge(value: float) -> dict[str, Any]:
    return {"compare": {"indicator": "adx", "period": 14, "op": ">=", "value": value}}


def _when(adx_min: float | None) -> dict[str, Any]:
    if adx_min is None:
        return _always_on()
    return _adx_ge(adx_min)


def _donchian_playbook(period: int, *, long_only: bool) -> dict[str, Any]:
    book: dict[str, Any] = {
        "long_entry": {
            "compare_indicators": {
                "left": "close",
                "right": {"indicator": "donchian_upper", "period": period},
                "op": ">=",
            }
        },
        "long_exit": {
            "compare_indicators": {
                "left": "close",
                "right": {"indicator": "donchian_mid", "period": period},
                "op": "<=",
            }
        },
    }
    if not long_only:
        book["short_entry"] = {
            "compare_indicators": {
                "left": "close",
                "right": {"indicator": "donchian_lower", "period": period},
                "op": "<=",
            }
        }
        book["short_exit"] = {
            "compare_indicators": {
                "left": "close",
                "right": {"indicator": "donchian_mid", "period": period},
                "op": ">=",
            }
        }
    return book


def _ema_playbook(fast: int, slow: int, *, long_only: bool) -> dict[str, Any]:
    book: dict[str, Any] = {
        "long_entry": {
            "cross": {
                "left": {"indicator": "ema", "period": fast},
                "right": {"indicator": "ema", "period": slow},
                "direction": "above",
            }
        },
        "long_exit": {
            "cross": {
                "left": {"indicator": "ema", "period": fast},
                "right": {"indicator": "ema", "period": slow},
                "direction": "below",
            }
        },
    }
    if not long_only:
        book["short_entry"] = {
            "cross": {
                "left": {"indicator": "ema", "period": fast},
                "right": {"indicator": "ema", "period": slow},
                "direction": "below",
            }
        }
        book["short_exit"] = {
            "cross": {
                "left": {"indicator": "ema", "period": fast},
                "right": {"indicator": "ema", "period": slow},
                "direction": "above",
            }
        }
    return book


def _make_case(
    family: str,
    label: str,
    when: dict[str, Any],
    playbook: dict[str, Any],
    *,
    min_regime_bars: int,
    stop_name: str,
    session: tuple[int, int, int],
) -> Case:
    risk = STOP_GRIDS[stop_name]
    if "short_entry" not in playbook:
        risk = {k: v for k, v in risk.items() if not k.startswith("short_")}
    start, end, flatten = session
    side = "long" if "short_entry" not in playbook else "ls"
    return Case(
        family=family,
        label=f"{label}|stop={stop_name}|min={min_regime_bars}|{side}",
        params={
            "default": "flat",
            "min_regime_bars": min_regime_bars,
            "entry_hour_start_utc": start,
            "entry_hour_end_utc": end,
            "flatten_hour_utc": flatten,
            "regimes": [{"name": "breakout", "when": when, "playbook": {**playbook, **risk}}],
        },
    )


def build_grid(density: str, *, long_only: bool, session: tuple[int, int, int]) -> list[Case]:
    if density == "quick":
        donchian_periods = [6, 10, 20, 55]
        ema_pairs = [(8, 21), (12, 26)]
        adx_mins: list[float | None] = [None, 20.0, 25.0]
        stops = ["none", "med", "wide"]
        mins = [2, 4]
    elif density == "full":
        donchian_periods = [4, 6, 10, 20, 40, 55]
        ema_pairs = [(5, 13), (8, 21), (12, 26), (20, 50)]
        adx_mins = [None, 18.0, 20.0, 25.0, 30.0]
        stops = ["none", "med", "wide"]
        mins = [2, 4, 6]
    else:  # medium
        donchian_periods = [6, 10, 20, 40, 55]
        ema_pairs = [(8, 21), (12, 26), (20, 50)]
        adx_mins = [None, 20.0, 25.0]
        stops = ["none", "med", "wide"]
        mins = [2, 4]

    cases: list[Case] = []
    for period, adx, stop, mb in itertools.product(donchian_periods, adx_mins, stops, mins):
        adx_s = "any" if adx is None else f">={adx:g}"
        cases.append(
            _make_case(
                "donchian",
                f"donchian_{period}|adx{adx_s}",
                _when(adx),
                _donchian_playbook(period, long_only=long_only),
                min_regime_bars=mb,
                stop_name=stop,
                session=session,
            )
        )
    for (fast, slow), adx, stop, mb in itertools.product(ema_pairs, adx_mins, stops, mins):
        adx_s = "any" if adx is None else f">={adx:g}"
        cases.append(
            _make_case(
                "ema",
                f"ema_{fast}_{slow}|adx{adx_s}",
                _when(adx),
                _ema_playbook(fast, slow, long_only=long_only),
                min_regime_bars=mb,
                stop_name=stop,
                session=session,
            )
        )
    return cases


def _parse_csv(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _trim(bars: list[Bar], max_bars: int | None) -> list[Bar]:
    if max_bars is None or len(bars) <= max_bars:
        return bars
    return bars[-max_bars:]


def _run_one(
    payload: tuple[str, str, Case, int, int | None],
) -> dict[str, Any]:
    overlay, timeframe, case, lookback, max_bars = payload
    settings = Settings()
    base = load_config(overlay=overlay)
    config = base.model_copy(
        update={"strategy": StrategyConfig(path=_REGIME_STRATEGY_PATH, params={})}
    )
    container = build_container(settings, config)
    symbol = config.trading.symbol
    exchange = config.trading.exchange
    rules = InstrumentRulesCache(Path(settings.data_dir), max_age_hours=24 * 30).load(
        exchange, symbol
    )
    if rules is None:
        raise RuntimeError(f"missing rules for {exchange}/{symbol}")
    bars = _trim(
        list(
            ParquetMarketDataRepository(Path(settings.data_dir), exchange=exchange).load_bars(
                symbol, timeframe
            )
        ),
        max_bars,
    )
    params = {**case.params, "lookback": lookback}
    run = build_backtest_engine(
        container, rules, symbol=symbol, timeframe=timeframe, strategy_params=params
    )
    try:
        result = run.engine.run(bars, timeframe)
    finally:
        run.teardown()
    report = build_performance_report(
        result,
        bars,
        min_round_trips=1,
        min_bars=1,
        min_daily_returns_for_sharpe=1,
        bootstrap_iterations=1,
        bootstrap_seed=config.analytics.bootstrap_seed,
        market_sma_period=config.analytics.market_sma_period,
    )
    m = report.metrics
    bh = (
        float(report.buy_and_hold_return_pct)
        if report.buy_and_hold_return_pct is not None
        else float("nan")
    )
    return {
        "overlay": overlay,
        "symbol": symbol,
        "tf": timeframe,
        "family": case.family,
        "label": case.label,
        "return_pct": float(m.total_return_pct),
        "maxdd_pct": float(m.max_drawdown_pct),
        "sharpe": float(m.sharpe_daily) if m.sharpe_daily is not None else None,
        "trips": m.round_trip_count,
        "win_rate": float(m.win_rate) if m.win_rate is not None else None,
        "profit_factor": float(m.profit_factor) if m.profit_factor is not None else None,
        "avg_pnl": float(m.avg_trade_pnl) if m.avg_trade_pnl is not None else None,
        "bh_pct": bh,
        "vs_bh": float(m.total_return_pct) - bh,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overlays", default="ig-eurusd,ig-dax-fut")
    parser.add_argument("--timeframes", default="1h")
    parser.add_argument("--grid", choices=("quick", "medium", "full"), default="quick")
    parser.add_argument(
        "--long-only",
        action="store_true",
        help="Disable shorts (long breakouts only).",
    )
    parser.add_argument("--max-bars", type=int, default=2500)
    parser.add_argument("--lookback", type=int, default=120)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--min-trips", type=int, default=10)
    parser.add_argument(
        "--csv",
        type=Path,
        default=Path("data/research/ig_session_breakout.csv"),
    )
    parser.add_argument("--no-discord", action="store_true")
    args = parser.parse_args()

    overlays = _parse_csv(args.overlays)
    timeframes = _parse_csv(args.timeframes)

    settings = Settings()
    jobs: list[tuple[str, str, Case, int, int | None]] = []
    for overlay in overlays:
        if overlay not in _SESSION:
            raise SystemExit(f"No session map for {overlay}; add to _SESSION")
        session = _SESSION[overlay]
        cases = build_grid(args.grid, long_only=args.long_only, session=session)
        config = load_config(overlay=overlay)
        symbol = config.trading.symbol
        rules = InstrumentRulesCache(Path(settings.data_dir), max_age_hours=24 * 30).load(
            config.trading.exchange, symbol
        )
        if rules is None:
            raise SystemExit(f"No rules for {symbol}")
        repo = ParquetMarketDataRepository(
            Path(settings.data_dir), exchange=config.trading.exchange
        )
        print(
            f"# {overlay} {symbol} cases={len(cases)} "
            f"session=entries[{session[0]},{session[1]}) flatten>={session[2]} "
            f"long_only={args.long_only} spread_bps={config.backtest.spread_bps} "
            f"cash={config.backtest.starting_cash}"
        )
        for timeframe in timeframes:
            bars = _trim(list(repo.load_bars(symbol, timeframe)), args.max_bars)
            if not bars:
                print(f"  {timeframe}: no bars")
                continue
            span = (bars[-1].timestamp - bars[0].timestamp) / timedelta(days=1)
            print(
                f"  {timeframe}: {len(bars)} bars "
                f"({bars[0].timestamp.date()}->{bars[-1].timestamp.date()}, ~{span:.0f}d)"
            )
            for case in cases:
                jobs.append((overlay, timeframe, case, args.lookback, args.max_bars))

    print(f"grid={args.grid} jobs={len(jobs)} workers={args.workers} lookback={args.lookback}")
    print(
        "Varies: Donchian period / EMA pair, ADX floor, ATR stops, min_regime_bars. "
        "Session gates always on. Funding still not modelled — flatten aims to avoid it."
    )

    rows: list[dict[str, Any]] = []
    if args.workers <= 1:
        for i, job in enumerate(jobs, start=1):
            row = _run_one(job)
            rows.append(row)
            if i == 1 or i % 25 == 0 or i == len(jobs):
                print(
                    f"  … {i}/{len(jobs)} {row['overlay']}/{row['tf']} "
                    f"{row['family']} ret={row['return_pct']:+.2f}% "
                    f"trips={row['trips']}",
                    flush=True,
                )
    else:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        pool = ProcessPoolExecutor(max_workers=args.workers)
        futures = []
        try:
            futures = [pool.submit(_run_one, job) for job in jobs]
            for done, fut in enumerate(as_completed(futures), start=1):
                row = fut.result()
                rows.append(row)
                if done == 1 or done % 25 == 0 or done == len(jobs):
                    print(
                        f"  … {done}/{len(jobs)} {row['overlay']}/{row['tf']} "
                        f"{row['family']} ret={row['return_pct']:+.2f}% "
                        f"trips={row['trips']}",
                        flush=True,
                    )
        except KeyboardInterrupt:
            print("\nInterrupted — cancelling workers…", flush=True)
            for fut in futures:
                fut.cancel()
            raise
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    ranked = [r for r in rows if r["trips"] >= args.min_trips]
    ranked.sort(key=lambda r: (r["return_pct"], r["sharpe"] or -99.0), reverse=True)

    header = (
        f"{'overlay':<14} {'tf':<4} {'family':<8} {'return%':>8} {'vs_bh':>8} "
        f"{'maxdd%':>8} {'sharpe':>7} {'trips':>5} {'win%':>6} {'bh%':>7}  label"
    )
    print(f"\n=== Top {args.top} by return (min_trips>={args.min_trips}) ===")
    print(header)
    print("-" * len(header))
    for r in ranked[: args.top]:
        sharpe = f"{r['sharpe']:.2f}" if r["sharpe"] is not None else "n/a"
        win = f"{r['win_rate'] * 100:.1f}" if r["win_rate"] is not None else "n/a"
        print(
            f"{r['overlay']:<14} {r['tf']:<4} {r['family']:<8} "
            f"{r['return_pct']:>8.2f} {r['vs_bh']:>8.2f} {r['maxdd_pct']:>8.2f} "
            f"{sharpe:>7} {r['trips']:>5} {win:>6} {r['bh_pct']:>7.2f}  {r['label']}"
        )

    print("\n=== Best per overlay/tf ===")
    best_lines: list[str] = []
    for overlay in overlays:
        for timeframe in timeframes:
            subset = [r for r in ranked if r["overlay"] == overlay and r["tf"] == timeframe]
            if not subset:
                line = f"{overlay}/{timeframe}: none"
                print(line)
                best_lines.append(line)
                continue
            best = subset[0]
            line = (
                f"{overlay}/{timeframe}: ret={best['return_pct']:+.2f}% "
                f"vs_bh={best['vs_bh']:+.2f}% trips={best['trips']}  {best['label']}"
            )
            print(line)
            best_lines.append(line)

    beat_bh = [r for r in ranked if r["vs_bh"] > 0 and r["return_pct"] > 0]
    print(f"\nPositive return AND beat B&H: {len(beat_bh)}/{len(ranked)} ranked")

    args.csv.parent.mkdir(parents=True, exist_ok=True)
    with args.csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else [])
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nWrote {len(rows)} rows -> {args.csv}")

    top_lines = [
        (
            f"{r['overlay']}/{r['tf']} {r['family']} "
            f"ret={r['return_pct']:+.2f}% trips={r['trips']} {r['label']}"
        )
        for r in ranked[:10]
    ]
    summary = "\n".join(
        [
            f"Research done: ig_session_breakout grid={args.grid}",
            f"overlays={','.join(overlays)} tfs={','.join(timeframes)} "
            f"jobs={len(jobs)} long_only={args.long_only}",
            "Session gates always on; funding not modelled.",
            "Top (by return):",
            *(top_lines or ["(none met min_trips)"]),
            "Best per overlay/tf:",
            *best_lines,
            f"beat_bh_and_green={len(beat_bh)} csv={args.csv}",
        ]
    )
    if notify_demo_research(summary, enabled=not args.no_discord):
        print("Posted summary to Discord demo webhook.", flush=True)


if __name__ == "__main__":
    try:
        import multiprocessing as _mp

        _mp.set_start_method("spawn", force=False)
    except RuntimeError:
        pass
    main()
