#!/usr/bin/env python3
"""IG index mean-reversion research — sweeps the small knobs.

Families (RegimeRouter, default=flat, ADX-low / always-on chop gate):

  rsi   — RSI cross out of oversold/overbought
  bb    — close touches Bollinger band, exit at mid
  stoch — %K cross out of oversold/overbought

What **varies** (see --grid):
  RSI period, RSI bands, BB period, BB num_std, Stoch k_period, Stoch bands,
  ADX chop ceiling (or none), ATR stop grid, min_regime_bars, long-only vs L/S,
  overlay, timeframe.

What stays fixed:
  ADX length=14, Stoch d_period=3, ATR stop period=14 when stops enabled,
  session hours per overlay (if --session).

Usage:
    uv run python scripts/research_ig_index_mean_rev.py
    uv run python scripts/research_ig_index_mean_rev.py --grid quick --timeframes 1h
    uv run python scripts/research_ig_index_mean_rev.py --overlays ig-us500-fut --families rsi,bb
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

_SESSION: dict[str, tuple[int, int, int]] = {
    "ig-dax": (7, 15, 15),
    "ig-dax-fut": (7, 15, 15),
    "ig-us500": (14, 20, 20),
    "ig-us500-fut": (14, 20, 20),
    "ig-ftse": (7, 15, 15),
}

STOP_GRIDS: dict[str, dict[str, float | int]] = {
    "none": {},
    "med": {
        "long_stop_atr": 2.0,
        "long_take_profit_atr": 3.0,
        "short_stop_atr": 2.0,
        "short_take_profit_atr": 3.0,
        "stop_atr_period": 14,
    },
    "wide": {
        "long_stop_atr": 3.0,
        "long_take_profit_atr": 4.5,
        "short_stop_atr": 3.0,
        "short_take_profit_atr": 4.5,
        "stop_atr_period": 14,
    },
}


@dataclass(frozen=True, slots=True)
class Case:
    family: str
    label: str
    params: dict[str, Any]


def _adx_lt(value: float) -> dict[str, Any]:
    return {"compare": {"indicator": "adx", "period": 14, "op": "<", "value": value}}


def _always_on() -> dict[str, Any]:
    # Trivially true: SMA(1) of close is the close itself and always > 0 for indices.
    return {"compare": {"indicator": "sma", "period": 1, "op": ">", "value": 0.0}}


def _chop_when(adx_max: float | None) -> dict[str, Any]:
    if adx_max is None:
        return _always_on()
    return _adx_lt(adx_max)


def _rsi_playbook(period: int, low: float, high: float, *, long_only: bool) -> dict[str, Any]:
    mid = (low + high) / 2.0
    rsi = {"indicator": "rsi", "period": period}
    book: dict[str, Any] = {
        "long_entry": {"cross": {"left": rsi, "right": low, "direction": "above"}},
        "long_exit": {"cross": {"left": rsi, "right": mid, "direction": "above"}},
    }
    if not long_only:
        book["short_entry"] = {"cross": {"left": rsi, "right": high, "direction": "below"}}
        book["short_exit"] = {"cross": {"left": rsi, "right": mid, "direction": "below"}}
    return book


def _bb_playbook(period: int, num_std: float, *, long_only: bool) -> dict[str, Any]:
    lower = {"indicator": "bb_lower", "period": period, "num_std": num_std}
    mid = {"indicator": "bb_mid", "period": period, "num_std": num_std}
    upper = {"indicator": "bb_upper", "period": period, "num_std": num_std}
    book: dict[str, Any] = {
        "long_entry": {"compare_indicators": {"left": "close", "right": lower, "op": "<="}},
        "long_exit": {"compare_indicators": {"left": "close", "right": mid, "op": ">="}},
    }
    if not long_only:
        book["short_entry"] = {"compare_indicators": {"left": "close", "right": upper, "op": ">="}}
        book["short_exit"] = {"compare_indicators": {"left": "close", "right": mid, "op": "<="}}
    return book


def _stoch_playbook(k_period: int, low: float, high: float, *, long_only: bool) -> dict[str, Any]:
    mid = (low + high) / 2.0
    k = {"indicator": "stoch_k", "k_period": k_period, "d_period": 3}
    book: dict[str, Any] = {
        "long_entry": {"cross": {"left": k, "right": low, "direction": "above"}},
        "long_exit": {"cross": {"left": k, "right": mid, "direction": "above"}},
    }
    if not long_only:
        book["short_entry"] = {"cross": {"left": k, "right": high, "direction": "below"}}
        book["short_exit"] = {"cross": {"left": k, "right": mid, "direction": "below"}}
    return book


def _make_case(
    family: str,
    label: str,
    when: dict[str, Any],
    playbook: dict[str, Any],
    *,
    min_regime_bars: int,
    stop_name: str,
    session: tuple[int, int, int] | None,
) -> Case:
    risk = STOP_GRIDS[stop_name]
    # Drop short_* stop keys when playbook is long-only.
    if "short_entry" not in playbook:
        risk = {k: v for k, v in risk.items() if not k.startswith("short_")}
    params: dict[str, Any] = {
        "default": "flat",
        "min_regime_bars": min_regime_bars,
        "regimes": [{"name": "mr", "when": when, "playbook": {**playbook, **risk}}],
    }
    if session is not None:
        start, end, flatten = session
        params["entry_hour_start_utc"] = start
        params["entry_hour_end_utc"] = end
        params["flatten_hour_utc"] = flatten
    side = "long" if "short_entry" not in playbook else "ls"
    return Case(
        family=family,
        label=f"{label}|stop={stop_name}|min={min_regime_bars}|{side}",
        params=params,
    )


def build_grid(
    density: str,
    *,
    families: set[str] | None,
    long_only: bool,
    session: tuple[int, int, int] | None,
) -> list[Case]:
    if density == "quick":
        rsi_periods = [7, 14]
        rsi_bands = [(25.0, 75.0), (30.0, 70.0)]
        bb_periods = [10, 20]
        bb_stds = [2.0, 2.5]
        stoch_ks = [5, 14]
        stoch_bands = [(20.0, 80.0), (30.0, 70.0)]
        adx_maxes: list[float | None] = [None, 20.0, 25.0]
        stops = ["none", "wide"]
        mins = [3, 6]
    elif density == "full":
        rsi_periods = [5, 7, 10, 14, 21]
        rsi_bands = [(20.0, 80.0), (25.0, 75.0), (30.0, 70.0), (35.0, 65.0)]
        bb_periods = [10, 15, 20, 30]
        bb_stds = [1.5, 2.0, 2.5, 3.0]
        stoch_ks = [5, 9, 14, 21]
        stoch_bands = [(15.0, 85.0), (20.0, 80.0), (25.0, 75.0), (30.0, 70.0)]
        adx_maxes = [None, 18.0, 22.0, 25.0, 30.0]
        stops = ["none", "med", "wide"]
        mins = [2, 4, 8]
    else:  # medium
        rsi_periods = [5, 7, 14, 21]
        rsi_bands = [(20.0, 80.0), (25.0, 75.0), (30.0, 70.0)]
        bb_periods = [10, 20, 30]
        bb_stds = [1.5, 2.0, 2.5]
        stoch_ks = [5, 14, 21]
        stoch_bands = [(20.0, 80.0), (25.0, 75.0), (30.0, 70.0)]
        adx_maxes = [None, 20.0, 25.0]
        stops = ["none", "med", "wide"]
        mins = [3, 6]

    want = families or {"rsi", "bb", "stoch"}
    cases: list[Case] = []

    if "rsi" in want:
        for period, (lo, hi), adx, stop, mb in itertools.product(
            rsi_periods, rsi_bands, adx_maxes, stops, mins
        ):
            adx_s = "any" if adx is None else f"<{adx:g}"
            cases.append(
                _make_case(
                    "rsi",
                    f"rsi{period}_{int(lo)}_{int(hi)}|adx{adx_s}",
                    _chop_when(adx),
                    _rsi_playbook(period, lo, hi, long_only=long_only),
                    min_regime_bars=mb,
                    stop_name=stop,
                    session=session,
                )
            )

    if "bb" in want:
        for period, std, adx, stop, mb in itertools.product(
            bb_periods, bb_stds, adx_maxes, stops, mins
        ):
            adx_s = "any" if adx is None else f"<{adx:g}"
            cases.append(
                _make_case(
                    "bb",
                    f"bb{period}_{std:g}|adx{adx_s}",
                    _chop_when(adx),
                    _bb_playbook(period, std, long_only=long_only),
                    min_regime_bars=mb,
                    stop_name=stop,
                    session=session,
                )
            )

    if "stoch" in want:
        for k_period, (lo, hi), adx, stop, mb in itertools.product(
            stoch_ks, stoch_bands, adx_maxes, stops, mins
        ):
            adx_s = "any" if adx is None else f"<{adx:g}"
            cases.append(
                _make_case(
                    "stoch",
                    f"stoch{k_period}_{int(lo)}_{int(hi)}|adx{adx_s}",
                    _chop_when(adx),
                    _stoch_playbook(k_period, lo, hi, long_only=long_only),
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
    parser.add_argument("--overlays", default="ig-dax-fut,ig-us500-fut")
    parser.add_argument("--timeframes", default="1h,4h")
    parser.add_argument("--grid", choices=("quick", "medium", "full"), default="quick")
    parser.add_argument(
        "--families",
        default="rsi,bb,stoch",
        help="Comma list: rsi,bb,stoch (default: all).",
    )
    parser.add_argument(
        "--long-only",
        action="store_true",
        help="Disable shorts (mean-rev long side only).",
    )
    parser.add_argument(
        "--session",
        action="store_true",
        help="Enable cash-hours entry window + flatten (per overlay).",
    )
    parser.add_argument("--max-bars", type=int, default=2500)
    parser.add_argument("--lookback", type=int, default=120)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--min-trips", type=int, default=10)
    parser.add_argument(
        "--csv",
        type=Path,
        default=Path("data/research/ig_index_mean_rev.csv"),
    )
    parser.add_argument("--no-discord", action="store_true")
    args = parser.parse_args()

    overlays = _parse_csv(args.overlays)
    timeframes = _parse_csv(args.timeframes)
    family_set = set(_parse_csv(args.families))

    settings = Settings()
    jobs: list[tuple[str, str, Case, int, int | None]] = []
    for overlay in overlays:
        if args.session and overlay not in _SESSION:
            raise SystemExit(f"No session map for {overlay}")
        session = _SESSION[overlay] if args.session else None
        cases = build_grid(
            args.grid,
            families=family_set,
            long_only=args.long_only,
            session=session,
        )
        if not cases:
            raise SystemExit("No cases after family filter.")
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
            f"session={session or 'off'} long_only={args.long_only} "
            f"spread_bps={config.backtest.spread_bps} cash={config.backtest.starting_cash}"
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

    print(
        f"grid={args.grid} families={sorted(family_set)} "
        f"jobs={len(jobs)} workers={args.workers} lookback={args.lookback}"
    )
    print(
        "Varies: RSI period/bands, BB period/std, Stoch k/bands, "
        "ADX chop ceiling, stops, min_regime_bars."
    )
    print("Fixed: ADX period=14, Stoch d=3, ATR period=14 (when stops on).")

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
        f"{'overlay':<12} {'tf':<4} {'family':<6} {'return%':>8} {'vs_bh':>8} "
        f"{'maxdd%':>8} {'sharpe':>7} {'trips':>5} {'win%':>6} {'bh%':>7}  label"
    )
    print(f"\n=== Top {args.top} by return (min_trips>={args.min_trips}) ===")
    print(header)
    print("-" * len(header))
    for r in ranked[: args.top]:
        sharpe = f"{r['sharpe']:.2f}" if r["sharpe"] is not None else "n/a"
        win = f"{r['win_rate'] * 100:.1f}" if r["win_rate"] is not None else "n/a"
        print(
            f"{r['overlay']:<12} {r['tf']:<4} {r['family']:<6} "
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
            f"Research done: ig_index_mean_rev grid={args.grid}",
            f"overlays={','.join(overlays)} tfs={','.join(timeframes)} "
            f"families={','.join(sorted(family_set))} jobs={len(jobs)} "
            f"long_only={args.long_only} session={args.session}",
            "Top (by return):",
            *(top_lines or ["(none met min_trips)"]),
            "Best per overlay/tf:",
            *best_lines,
            f"csv={args.csv}",
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
