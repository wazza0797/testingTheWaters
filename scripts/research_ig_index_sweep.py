#!/usr/bin/env python3
"""IG index CFD parameter sweep across overlays (US 500 / DAX / FTSE).

Unlike the fixed-recipe runner, this expands configurable knobs: EMA/Donchian/
SuperTrend/MACD lengths, ADX gates (trend vs chop), min_regime_bars, ATR
stops, plus RSI / Bollinger mean-reversion in low-ADX regimes.

Overnight funding is **not** modelled. Yahoo stand-in bars are indicative.

Usage:
    uv run python scripts/research_ig_index_sweep.py
    uv run python scripts/research_ig_index_sweep.py --overlays ig-us500,ig-dax
    uv run python scripts/research_ig_index_sweep.py --grid quick --timeframes 1h
    uv run python scripts/research_ig_index_sweep.py --max-bars 3000 --top 20
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

STOP_GRIDS: dict[str, dict[str, float | int]] = {
    "none": {},
    "wide": {
        "long_stop_atr": 3.0,
        "long_take_profit_atr": 5.0,
        "short_stop_atr": 3.0,
        "short_take_profit_atr": 5.0,
        "stop_atr_period": 14,
    },
    "med": {
        "long_stop_atr": 2.0,
        "long_take_profit_atr": 3.5,
        "short_stop_atr": 2.0,
        "short_take_profit_atr": 3.5,
        "stop_atr_period": 14,
    },
}


@dataclass(frozen=True, slots=True)
class SweepCase:
    family: str
    label: str
    params: dict[str, Any]


def _adx_ge(value: float, period: int = 14) -> dict[str, Any]:
    return {"compare": {"indicator": "adx", "period": period, "op": ">=", "value": value}}


def _adx_lt(value: float, period: int = 14) -> dict[str, Any]:
    return {"compare": {"indicator": "adx", "period": period, "op": "<", "value": value}}


def _regime(name: str, when: dict[str, Any], playbook: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "when": when, "playbook": playbook}


def _ema_playbook(fast: int, slow: int) -> dict[str, Any]:
    return {
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
        "short_entry": {
            "cross": {
                "left": {"indicator": "ema", "period": fast},
                "right": {"indicator": "ema", "period": slow},
                "direction": "below",
            }
        },
        "short_exit": {
            "cross": {
                "left": {"indicator": "ema", "period": fast},
                "right": {"indicator": "ema", "period": slow},
                "direction": "above",
            }
        },
    }


def _donchian_playbook(period: int) -> dict[str, Any]:
    return {
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
        "short_entry": {
            "compare_indicators": {
                "left": "close",
                "right": {"indicator": "donchian_lower", "period": period},
                "op": "<=",
            }
        },
        "short_exit": {
            "compare_indicators": {
                "left": "close",
                "right": {"indicator": "donchian_mid", "period": period},
                "op": ">=",
            }
        },
    }


def _supertrend_playbook(period: int, multiplier: float) -> dict[str, Any]:
    st = {"indicator": "supertrend_dir", "period": period, "multiplier": multiplier}
    return {
        "long_entry": {"cross": {"left": st, "right": 0.0, "direction": "above"}},
        "long_exit": {"cross": {"left": st, "right": 0.0, "direction": "below"}},
        "short_entry": {"cross": {"left": st, "right": 0.0, "direction": "below"}},
        "short_exit": {"cross": {"left": st, "right": 0.0, "direction": "above"}},
    }


def _macd_playbook(fast: int, slow: int, signal: int) -> dict[str, Any]:
    left = {
        "indicator": "macd",
        "fast_period": fast,
        "slow_period": slow,
        "signal_period": signal,
    }
    right = {
        "indicator": "macd_signal",
        "fast_period": fast,
        "slow_period": slow,
        "signal_period": signal,
    }
    return {
        "long_entry": {"cross": {"left": left, "right": right, "direction": "above"}},
        "long_exit": {"cross": {"left": left, "right": right, "direction": "below"}},
        "short_entry": {"cross": {"left": left, "right": right, "direction": "below"}},
        "short_exit": {"cross": {"left": left, "right": right, "direction": "above"}},
    }


def _rsi_mean_rev_playbook(period: int, low: float, high: float) -> dict[str, Any]:
    # Long when RSI crosses up through oversold; exit mid. Short mirror.
    mid = (low + high) / 2.0
    rsi = {"indicator": "rsi", "period": period}
    return {
        "long_entry": {"cross": {"left": rsi, "right": low, "direction": "above"}},
        "long_exit": {"cross": {"left": rsi, "right": mid, "direction": "above"}},
        "short_entry": {"cross": {"left": rsi, "right": high, "direction": "below"}},
        "short_exit": {"cross": {"left": rsi, "right": mid, "direction": "below"}},
    }


def _bb_mean_rev_playbook(period: int, std: float) -> dict[str, Any]:
    lower = {"indicator": "bb_lower", "period": period, "num_std": std}
    mid = {"indicator": "bb_mid", "period": period, "num_std": std}
    upper = {"indicator": "bb_upper", "period": period, "num_std": std}
    return {
        "long_entry": {"compare_indicators": {"left": "close", "right": lower, "op": "<="}},
        "long_exit": {"compare_indicators": {"left": "close", "right": mid, "op": ">="}},
        "short_entry": {"compare_indicators": {"left": "close", "right": upper, "op": ">="}},
        "short_exit": {"compare_indicators": {"left": "close", "right": mid, "op": "<="}},
    }


def _merge_risk(playbook: dict[str, Any], risk: dict[str, float | int]) -> dict[str, Any]:
    if not risk:
        return playbook
    return {**playbook, **risk}


def _case(
    family: str,
    label: str,
    when: dict[str, Any],
    playbook: dict[str, Any],
    *,
    min_regime_bars: int,
    stop_name: str,
) -> SweepCase:
    risk = STOP_GRIDS[stop_name]
    params = {
        "default": "flat",
        "min_regime_bars": min_regime_bars,
        "regimes": [_regime("active", when, _merge_risk(playbook, risk))],
    }
    return SweepCase(
        family=family, label=f"{label}|stop={stop_name}|min={min_regime_bars}", params=params
    )


def build_grid(density: str) -> list[SweepCase]:
    """Build sweep cases. `quick` ≈ 40, `medium` ≈ 120, `full` ≈ 220."""
    cases: list[SweepCase] = []

    if density == "quick":
        ema_pairs = [(12, 26), (20, 50)]
        adx_trend = [20, 25]
        donchian_periods = [20, 55]
        st_params = [(10, 3.0), (14, 2.5)]
        min_bars = [4, 8]
        stops = ["none", "wide"]
        rsi_bands = [(30.0, 70.0)]
        adx_chop = [20.0]
        include_macd = True
        include_bb = True
    elif density == "full":
        ema_pairs = [(8, 21), (12, 26), (20, 50), (50, 200)]
        adx_trend = [18, 22, 25, 30]
        donchian_periods = [10, 20, 40, 55]
        st_params = [(7, 2.0), (10, 2.0), (10, 3.0), (14, 3.0), (14, 3.5)]
        min_bars = [3, 6, 12]
        stops = ["none", "med", "wide"]
        rsi_bands = [(30.0, 70.0), (25.0, 75.0), (20.0, 80.0)]
        adx_chop = [18.0, 22.0, 25.0]
        include_macd = True
        include_bb = True
    else:  # medium
        ema_pairs = [(8, 21), (12, 26), (20, 50)]
        adx_trend = [20, 25, 30]
        donchian_periods = [10, 20, 55]
        st_params = [(10, 2.0), (10, 3.0), (14, 2.5), (14, 3.5)]
        min_bars = [3, 6, 12]
        stops = ["none", "wide"]
        rsi_bands = [(30.0, 70.0), (25.0, 75.0)]
        adx_chop = [20.0, 25.0]
        include_macd = True
        include_bb = True

    for (fast, slow), adx, mb, stop in itertools.product(ema_pairs, adx_trend, min_bars, stops):
        cases.append(
            _case(
                "ema",
                f"ema_{fast}_{slow}|adx>={adx}",
                _adx_ge(float(adx)),
                _ema_playbook(fast, slow),
                min_regime_bars=mb,
                stop_name=stop,
            )
        )

    for period, adx, mb, stop in itertools.product(donchian_periods, adx_trend, min_bars, stops):
        cases.append(
            _case(
                "donchian",
                f"donchian_{period}|adx>={adx}",
                _adx_ge(float(adx)),
                _donchian_playbook(period),
                min_regime_bars=mb,
                stop_name=stop,
            )
        )

    for (period, mult), adx, mb, stop in itertools.product(
        st_params, adx_trend[:2], min_bars[:2], stops
    ):
        cases.append(
            _case(
                "supertrend",
                f"st_{period}x{mult}|adx>={adx}",
                _adx_ge(float(adx)),
                _supertrend_playbook(period, mult),
                min_regime_bars=mb,
                stop_name=stop,
            )
        )

    if include_macd:
        for adx, mb, stop in itertools.product(adx_trend, min_bars[:2], stops):
            cases.append(
                _case(
                    "macd",
                    f"macd_12_26_9|adx>={adx}",
                    _adx_ge(float(adx)),
                    _macd_playbook(12, 26, 9),
                    min_regime_bars=mb,
                    stop_name=stop,
                )
            )

    for (low, high), adx_max, mb, stop in itertools.product(
        rsi_bands, adx_chop, min_bars[:2], ["wide", "med"] if density == "full" else ["wide"]
    ):
        if stop not in STOP_GRIDS:
            continue
        cases.append(
            _case(
                "rsi_mr",
                f"rsi14_{int(low)}_{int(high)}|adx<{adx_max}",
                _adx_lt(float(adx_max)),
                _rsi_mean_rev_playbook(14, low, high),
                min_regime_bars=mb,
                stop_name=stop,
            )
        )

    if include_bb:
        for adx_max, mb, stop in itertools.product(adx_chop, min_bars[:2], stops):
            cases.append(
                _case(
                    "bb_mr",
                    f"bb_20_2|adx<{adx_max}",
                    _adx_lt(float(adx_max)),
                    _bb_mean_rev_playbook(20, 2.0),
                    min_regime_bars=mb,
                    stop_name=stop,
                )
            )

    return cases


def _parse_csv(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _trim_bars(bars: list[Bar], max_bars: int | None) -> list[Bar]:
    if max_bars is None or len(bars) <= max_bars:
        return bars
    return bars[-max_bars:]


def _run_case(
    *,
    overlay: str,
    timeframe: str,
    case: SweepCase,
    lookback: int,
    max_bars: int | None,
) -> dict[str, Any]:
    """Run one backtest in-process (safe for ProcessPoolExecutor workers)."""
    settings = Settings()
    base_config = load_config(overlay=overlay)
    config = base_config.model_copy(
        update={"strategy": StrategyConfig(path=_REGIME_STRATEGY_PATH, params={})}
    )
    container = build_container(settings, config)
    symbol = config.trading.symbol
    exchange = config.trading.exchange
    repository = ParquetMarketDataRepository(Path(settings.data_dir), exchange=exchange)
    rules = InstrumentRulesCache(Path(settings.data_dir), max_age_hours=24 * 30).load(
        exchange, symbol
    )
    if rules is None:
        raise RuntimeError(f"missing instrument rules for {exchange}/{symbol}")

    bars = _trim_bars(list(repository.load_bars(symbol, timeframe)), max_bars)
    if not bars:
        return {
            "overlay": overlay,
            "symbol": symbol,
            "tf": timeframe,
            "family": case.family,
            "label": case.label,
            "return_pct": float("nan"),
            "maxdd_pct": float("nan"),
            "sharpe": None,
            "trips": 0,
            "win_rate": None,
            "profit_factor": None,
            "avg_pnl": None,
            "bh_pct": float("nan"),
        }

    params = {**case.params, "lookback": lookback}
    run = build_backtest_engine(
        container,
        rules,
        symbol=symbol,
        timeframe=timeframe,
        strategy_params=params,
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
    }


def _run_case_star(payload: tuple[str, str, SweepCase, int, int | None]) -> dict[str, Any]:
    overlay, timeframe, case, lookback, max_bars = payload
    return _run_case(
        overlay=overlay,
        timeframe=timeframe,
        case=case,
        lookback=lookback,
        max_bars=max_bars,
    )


def _run_jobs_in_pool(
    jobs: list[tuple[str, str, SweepCase, int, int | None]],
    *,
    workers: int,
) -> list[dict[str, Any]]:
    """Submit jobs to a process pool and always tear workers down.

    Cancels outstanding futures on KeyboardInterrupt / unexpected exit so
    orphaned `multiprocessing.spawn` children do not keep burning CPU after
    the parent is killed or Ctrl-C'd.
    """
    from concurrent.futures import ProcessPoolExecutor, as_completed

    rows: list[dict[str, Any]] = []
    pool = ProcessPoolExecutor(max_workers=workers)
    futures = []
    try:
        futures = [pool.submit(_run_case_star, job) for job in jobs]
        for done, fut in enumerate(as_completed(futures), start=1):
            row = fut.result()
            rows.append(row)
            if done == 1 or done % 25 == 0 or done == len(jobs):
                print(
                    f"  … {done}/{len(jobs)} {row['overlay']}/{row['tf']} "
                    f"{row['family']} ret={row['return_pct']:+.2f}% trips={row['trips']}",
                    flush=True,
                )
    except KeyboardInterrupt:
        print(
            "\nInterrupted — cancelling worker pool "
            f"({sum(1 for f in futures if not f.done())} outstanding)…",
            flush=True,
        )
        for fut in futures:
            fut.cancel()
        raise
    finally:
        # cancel_futures=True (3.9+) drops queued work; wait=False returns
        # immediately so Ctrl-C feels responsive. Workers still exit.
        pool.shutdown(wait=False, cancel_futures=True)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--overlays",
        default="ig-us500,ig-dax",
        help="Comma-separated overlays (default: ig-us500,ig-dax).",
    )
    parser.add_argument(
        "--timeframes",
        default="1h,4h",
        help="Comma-separated timeframes (default: 1h,4h).",
    )
    parser.add_argument(
        "--grid",
        choices=("quick", "medium", "full"),
        default="medium",
        help="Parameter grid density (default: medium).",
    )
    parser.add_argument(
        "--families",
        default="",
        help="Optional family filter, e.g. ema,donchian,rsi_mr (default: all).",
    )
    parser.add_argument("--max-bars", type=int, default=None, help="Trailing-bar cap.")
    parser.add_argument("--lookback", type=int, default=220, help="Indicator lookback.")
    parser.add_argument("--top", type=int, default=15, help="Top-N rows to print.")
    parser.add_argument(
        "--min-trips",
        type=int,
        default=8,
        help="Drop cases with fewer round trips from the ranking (default: 8).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Process pool size (default: 1). Use 4-8 for medium/full grids.",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="Optional path to write all results as CSV.",
    )
    parser.add_argument(
        "--no-discord",
        action="store_true",
        help="Skip posting the end-of-run summary to DISCORD_DEMO_WEBHOOK_URL.",
    )
    args = parser.parse_args()

    overlays = _parse_csv(args.overlays)
    timeframes = _parse_csv(args.timeframes)
    family_filter = set(_parse_csv(args.families)) if args.families else None
    all_cases = build_grid(args.grid)
    if family_filter is not None:
        all_cases = [c for c in all_cases if c.family in family_filter]
    if not all_cases:
        raise SystemExit("No sweep cases after filters.")

    settings = Settings()
    jobs: list[tuple[str, str, SweepCase, int, int | None]] = []
    for overlay in overlays:
        config = load_config(overlay=overlay)
        symbol = config.trading.symbol
        exchange = config.trading.exchange
        rules = InstrumentRulesCache(Path(settings.data_dir), max_age_hours=24 * 30).load(
            exchange, symbol
        )
        if rules is None:
            raise SystemExit(
                f"No cached instrument rules for {exchange}/{symbol} — run "
                f"`uv run trading-platform download-data --overlay {overlay} --days 1` once."
            )
        repository = ParquetMarketDataRepository(Path(settings.data_dir), exchange=exchange)
        print(
            f"# {overlay} symbol={symbol} spread_bps={config.backtest.spread_bps} "
            f"vol_flag={config.backtest.assume_full_liquidity_when_no_volume}"
        )
        for timeframe in timeframes:
            bars = _trim_bars(list(repository.load_bars(symbol, timeframe)), args.max_bars)
            if not bars:
                print(f"{overlay:<10} {timeframe:<4} (no bars)")
                continue
            span_days = (bars[-1].timestamp - bars[0].timestamp) / timedelta(days=1)
            print(
                f"# {timeframe}: {len(bars)} bars "
                f"({bars[0].timestamp.date()} -> {bars[-1].timestamp.date()}, ~{span_days:.0f}d)"
            )
            for case in all_cases:
                jobs.append((overlay, timeframe, case, args.lookback, args.max_bars))

    print(
        f"grid={args.grid} cases/tf={len(all_cases)} jobs={len(jobs)} "
        f"workers={args.workers} lookback={args.lookback}"
    )
    print("NOTE: overnight funding not modelled — multi-day holds are indicative only.")

    header = (
        f"{'overlay':<10} {'tf':<4} {'family':<10} {'return%':>8} {'maxdd%':>8} "
        f"{'sharpe':>7} {'trips':>5} {'win%':>6} {'pf':>6} {'avg_pnl':>9} {'bh%':>7}  label"
    )
    print(header)
    print("-" * len(header))

    rows: list[dict[str, Any]] = []
    if args.workers <= 1:
        for i, job in enumerate(jobs, start=1):
            row = _run_case_star(job)
            rows.append(row)
            if i == 1 or i % 25 == 0 or i == len(jobs):
                print(
                    f"  … {i}/{len(jobs)} {row['overlay']}/{row['tf']} "
                    f"{row['family']} ret={row['return_pct']:+.2f}% trips={row['trips']}",
                    flush=True,
                )
    else:
        rows.extend(_run_jobs_in_pool(jobs, workers=args.workers))

    ranked = [
        r for r in rows if r["trips"] >= args.min_trips and r["return_pct"] == r["return_pct"]
    ]
    ranked.sort(key=lambda r: (r["return_pct"], r["sharpe"] or -99.0), reverse=True)

    print(f"\n=== Top {args.top} by return (min_trips>={args.min_trips}) ===")
    print(header)
    print("-" * len(header))
    for r in ranked[: args.top]:
        sharpe = f"{r['sharpe']:.2f}" if r["sharpe"] is not None else "n/a"
        win = f"{r['win_rate'] * 100:.1f}" if r["win_rate"] is not None else "n/a"
        pf = f"{r['profit_factor']:.2f}" if r["profit_factor"] is not None else "n/a"
        avg = f"{r['avg_pnl']:.2f}" if r["avg_pnl"] is not None else "n/a"
        print(
            f"{r['overlay']:<10} {r['tf']:<4} {r['family']:<10} "
            f"{r['return_pct']:>8.2f} {r['maxdd_pct']:>8.2f} {sharpe:>7} "
            f"{r['trips']:>5} {win:>6} {pf:>6} {avg:>9} {r['bh_pct']:>7.2f}  {r['label']}"
        )

    print("\n=== Best per overlay/tf ===")
    best_lines: list[str] = []
    for overlay in overlays:
        for timeframe in timeframes:
            subset = [r for r in ranked if r["overlay"] == overlay and r["tf"] == timeframe]
            if not subset:
                line = f"{overlay}/{timeframe}: no cases met min_trips"
                print(line)
                best_lines.append(line)
                continue
            best = subset[0]
            line = (
                f"{overlay}/{timeframe}: best={best['return_pct']:+.2f}% "
                f"(bh={best['bh_pct']:+.2f}%) family={best['family']} "
                f"trips={best['trips']}  {best['label']}"
            )
            print(line)
            best_lines.append(line)

    csv_note = ""
    if args.csv is not None and rows:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nWrote {len(rows)} rows -> {args.csv}")
        csv_note = f"csv={args.csv}"

    top_lines = []
    for r in ranked[:10]:
        top_lines.append(
            f"{r['overlay']}/{r['tf']} {r['family']} "
            f"ret={r['return_pct']:+.2f}% trips={r['trips']} {r['label']}"
        )
    summary = "\n".join(
        [
            f"Research done: ig_index_sweep grid={args.grid}",
            f"overlays={','.join(overlays)} tfs={','.join(timeframes)} "
            f"jobs={len(jobs)} workers={args.workers}",
            "Top (by return):",
            *(top_lines or ["(none met min_trips)"]),
            "Best per overlay/tf:",
            *best_lines,
            *([csv_note] if csv_note else []),
        ]
    )
    if notify_demo_research(summary, enabled=not args.no_discord):
        print("Posted summary to Discord demo webhook.", flush=True)


if __name__ == "__main__":
    # Required on macOS/Windows so pool workers do not re-exec main().
    try:
        import multiprocessing as _mp

        _mp.set_start_method("spawn", force=False)
    except RuntimeError:
        pass
    main()
