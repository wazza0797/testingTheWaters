#!/usr/bin/env python3
"""Long-biased + cash-hours IG index research (post MACD kill).

Hypothesis: symmetric long/short on cash indices fights the bullish drift and
pays overnight funding on multi-day shorts. This runner:

- long-only playbooks (no shorts)
- bull filter: close > SMA200 (and optional ADX floor)
- cash-session entry window + flatten hour (UTC approximations)

Session windows (approx, ignores DST):
  DAX / Europe cash: entries 07–15 UTC, flatten from 15 UTC
  US 500 cash:        entries 14–20 UTC, flatten from 20 UTC

Usage:
    uv run python scripts/research_ig_index_long_bias.py
    uv run python scripts/research_ig_index_long_bias.py --overlays ig-dax-fut,ig-us500-fut --workers 4
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
    "ig-dax": (7, 15, 15),
    "ig-dax-fut": (7, 15, 15),
    "ig-us500": (14, 20, 20),
    "ig-us500-fut": (14, 20, 20),
    "ig-ftse": (7, 15, 15),
}

STOP_GRIDS: dict[str, dict[str, float | int]] = {
    "none": {},
    "wide": {
        "long_stop_atr": 3.0,
        "long_take_profit_atr": 5.0,
        "stop_atr_period": 14,
    },
}


@dataclass(frozen=True, slots=True)
class Case:
    family: str
    label: str
    params: dict[str, Any]


def _bull_when(*, adx_min: float | None) -> dict[str, Any]:
    parts: list[dict[str, Any]] = [
        {
            "compare_indicators": {
                "left": "close",
                "right": {"indicator": "sma", "period": 200},
                "op": ">",
            }
        }
    ]
    if adx_min is not None:
        parts.append({"compare": {"indicator": "adx", "period": 14, "op": ">=", "value": adx_min}})
    if len(parts) == 1:
        return parts[0]
    return {"all": parts}


def _ema_long(fast: int, slow: int) -> dict[str, Any]:
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
    }


def _macd_long() -> dict[str, Any]:
    left = {"indicator": "macd", "fast_period": 12, "slow_period": 26, "signal_period": 9}
    right = {
        "indicator": "macd_signal",
        "fast_period": 12,
        "slow_period": 26,
        "signal_period": 9,
    }
    return {
        "long_entry": {"cross": {"left": left, "right": right, "direction": "above"}},
        "long_exit": {"cross": {"left": left, "right": right, "direction": "below"}},
    }


def _donchian_long(period: int) -> dict[str, Any]:
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
    }


def build_cases(*, session: tuple[int, int, int], use_session: bool) -> list[Case]:
    entry_start, entry_end, flatten = session
    session_params: dict[str, Any] = {}
    if use_session:
        session_params = {
            "entry_hour_start_utc": entry_start,
            "entry_hour_end_utc": entry_end,
            "flatten_hour_utc": flatten,
        }

    cases: list[Case] = []
    for (fast, slow), adx, stop, mb in itertools.product(
        [(12, 26), (20, 50)],
        [None, 20.0, 25.0],
        ["none", "wide"],
        [4, 8],
    ):
        playbook = {**_ema_long(fast, slow), **STOP_GRIDS[stop]}
        adx_s = "any" if adx is None else f">={adx:g}"
        cases.append(
            Case(
                family="ema",
                label=f"ema_{fast}_{slow}|sma200|adx{adx_s}|stop={stop}|min={mb}",
                params={
                    "default": "flat",
                    "min_regime_bars": mb,
                    "regimes": [
                        {
                            "name": "bull",
                            "when": _bull_when(adx_min=adx),
                            "playbook": playbook,
                        }
                    ],
                    **session_params,
                },
            )
        )

    for adx, stop, mb in itertools.product([None, 20.0, 25.0], ["none", "wide"], [4, 8]):
        playbook = {**_macd_long(), **STOP_GRIDS[stop]}
        adx_s = "any" if adx is None else f">={adx:g}"
        cases.append(
            Case(
                family="macd",
                label=f"macd_12_26_9|sma200|adx{adx_s}|stop={stop}|min={mb}",
                params={
                    "default": "flat",
                    "min_regime_bars": mb,
                    "regimes": [
                        {
                            "name": "bull",
                            "when": _bull_when(adx_min=adx),
                            "playbook": playbook,
                        }
                    ],
                    **session_params,
                },
            )
        )

    for period, adx, stop, mb in itertools.product(
        [20, 55], [None, 20.0], ["none", "wide"], [4, 8]
    ):
        playbook = {**_donchian_long(period), **STOP_GRIDS[stop]}
        adx_s = "any" if adx is None else f">={adx:g}"
        cases.append(
            Case(
                family="donchian",
                label=f"donchian_{period}|sma200|adx{adx_s}|stop={stop}|min={mb}",
                params={
                    "default": "flat",
                    "min_regime_bars": mb,
                    "regimes": [
                        {
                            "name": "bull",
                            "when": _bull_when(adx_min=adx),
                            "playbook": playbook,
                        }
                    ],
                    **session_params,
                },
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
    payload: tuple[str, str, Case, int, int | None, bool],
) -> dict[str, Any]:
    overlay, timeframe, case, lookback, max_bars, _use_session = payload
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
    parser.add_argument("--overlays", default="ig-dax,ig-us500")
    parser.add_argument("--timeframes", default="1h,4h")
    parser.add_argument("--max-bars", type=int, default=3000)
    parser.add_argument("--lookback", type=int, default=250)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--min-trips", type=int, default=6)
    parser.add_argument(
        "--no-session",
        action="store_true",
        help="Disable cash-hours entry/flatten gates (long-bias only).",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=Path("data/research/ig_index_long_bias.csv"),
    )
    parser.add_argument(
        "--no-discord",
        action="store_true",
        help="Skip posting the end-of-run summary to DISCORD_DEMO_WEBHOOK_URL.",
    )
    args = parser.parse_args()

    overlays = _parse_csv(args.overlays)
    timeframes = _parse_csv(args.timeframes)
    use_session = not args.no_session

    jobs: list[tuple[str, str, Case, int, int | None, bool]] = []
    settings = Settings()
    for overlay in overlays:
        if overlay not in _SESSION:
            raise SystemExit(f"No session map for {overlay}; add to _SESSION")
        config = load_config(overlay=overlay)
        symbol = config.trading.symbol
        rules = InstrumentRulesCache(Path(settings.data_dir), max_age_hours=24 * 30).load(
            config.trading.exchange, symbol
        )
        if rules is None:
            raise SystemExit(f"No rules for {symbol}")
        cases = build_cases(session=_SESSION[overlay], use_session=use_session)
        repo = ParquetMarketDataRepository(
            Path(settings.data_dir), exchange=config.trading.exchange
        )
        print(
            f"# {overlay} {symbol} session={_SESSION[overlay] if use_session else 'off'} "
            f"cases={len(cases)} cash={config.backtest.starting_cash}"
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
                jobs.append((overlay, timeframe, case, args.lookback, args.max_bars, use_session))

    print(f"jobs={len(jobs)} workers={args.workers} lookback={args.lookback}")
    print("NOTE: overnight funding still not modelled; session flatten aims to reduce holds.")

    rows: list[dict[str, Any]] = []
    if args.workers <= 1:
        for i, job in enumerate(jobs, start=1):
            row = _run_one(job)
            rows.append(row)
            if i == 1 or i % 25 == 0 or i == len(jobs):
                print(
                    f"  … {i}/{len(jobs)} {row['overlay']}/{row['tf']} "
                    f"{row['family']} ret={row['return_pct']:+.2f}% vs_bh={row['vs_bh']:+.2f}",
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
                        f"vs_bh={row['vs_bh']:+.2f}",
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
    # Prefer beating (or losing less to) buy-and-hold, then raw return.
    ranked.sort(key=lambda r: (r["vs_bh"], r["return_pct"]), reverse=True)

    header = (
        f"{'overlay':<10} {'tf':<4} {'family':<10} {'return%':>8} {'vs_bh':>8} "
        f"{'maxdd%':>8} {'sharpe':>7} {'trips':>5} {'bh%':>7}  label"
    )
    print(f"\n=== Top {args.top} by return−B&H (min_trips>={args.min_trips}) ===")
    print(header)
    print("-" * len(header))
    for r in ranked[: args.top]:
        sharpe = f"{r['sharpe']:.2f}" if r["sharpe"] is not None else "n/a"
        print(
            f"{r['overlay']:<10} {r['tf']:<4} {r['family']:<10} "
            f"{r['return_pct']:>8.2f} {r['vs_bh']:>8.2f} {r['maxdd_pct']:>8.2f} "
            f"{sharpe:>7} {r['trips']:>5} {r['bh_pct']:>7.2f}  {r['label']}"
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

    top_lines = []
    for r in ranked[:10]:
        top_lines.append(
            f"{r['overlay']}/{r['tf']} {r['family']} "
            f"ret={r['return_pct']:+.2f}% vs_bh={r['vs_bh']:+.2f}% "
            f"trips={r['trips']} {r['label']}"
        )
    summary = "\n".join(
        [
            "Research done: ig_index_long_bias",
            f"overlays={','.join(overlays)} tfs={','.join(timeframes)} "
            f"jobs={len(jobs)} session={'on' if use_session else 'off'}",
            "Top (by vs_bh):",
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
