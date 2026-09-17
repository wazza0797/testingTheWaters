#!/usr/bin/env python3
"""Full-history + rolling-OOS validation for session-breakout screen hits.

Candidates (quick screen on trailing 2500 bars; green and beat B&H on EURUSD 1h):

  donchian_10|adx>=25|stop=none|min=2|ls
  donchian_20|adx>=25|stop=none|min=2|ls
  ema_12_26|adx>=20|stop=none|min=2|ls
  donchian_10|adx>=25|stop=med|min=2|ls

Session always on: entries [7, 17) UTC, flatten from 21 UTC (pre-funding).

Usage:
    uv run python scripts/validate_session_breakout.py
    uv run python scripts/validate_session_breakout.py --only donchian10 --no-discord
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from trading_platform.analytics.report import build_performance_report
from trading_platform.analytics.trades import reconstruct_round_trips
from trading_platform.backtesting.walk_forward import iter_walk_forward_windows, stitch_oos_equity
from trading_platform.config.loader import StrategyConfig, load_config
from trading_platform.config.settings import Settings
from trading_platform.container import build_backtest_engine, build_container
from trading_platform.market_data.instrument_rules_cache import InstrumentRulesCache
from trading_platform.market_data.repository.parquet import ParquetMarketDataRepository
from trading_platform.notifications.research import notify_demo_research

_REGIME_STRATEGY_PATH = "trading_platform.strategies.examples.regime_router:RegimeRouterStrategy"
_OVERLAY = "ig-eurusd"
_SYMBOL = "CS.D.EURUSD.MINI.IP"
_TIMEFRAME = "1h"
_LOOKBACK = 120

# FX session: London day entries, flatten before ~22:00 London funding.
_ENTRY_START = 7
_ENTRY_END = 17
_FLATTEN = 21

# ~90d IS spacer / ~30d OOS at ~17 FX 1h bars/day.
_IS_BARS = 1500
_OOS_BARS = 500
_STEP_BARS = 1000

_STOP_MED = {
    "long_stop_atr": 1.5,
    "long_take_profit_atr": 3.0,
    "short_stop_atr": 1.5,
    "short_take_profit_atr": 3.0,
    "stop_atr_period": 14,
}


@dataclass(frozen=True, slots=True)
class Candidate:
    key: str
    label: str
    params: dict[str, Any]


def _adx_ge(value: float) -> dict[str, Any]:
    return {"compare": {"indicator": "adx", "period": 14, "op": ">=", "value": value}}


def _donchian(period: int) -> dict[str, Any]:
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


def _ema(fast: int, slow: int) -> dict[str, Any]:
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


def _regime(
    playbook: dict[str, Any], *, when: dict[str, Any], min_regime_bars: int
) -> dict[str, Any]:
    return {
        "default": "flat",
        "min_regime_bars": min_regime_bars,
        "lookback": _LOOKBACK,
        "entry_hour_start_utc": _ENTRY_START,
        "entry_hour_end_utc": _ENTRY_END,
        "flatten_hour_utc": _FLATTEN,
        "regimes": [{"name": "breakout", "when": when, "playbook": playbook}],
    }


_CANDIDATES: list[Candidate] = [
    Candidate(
        key="donchian10",
        label="donchian_10|adx>=25|stop=none|min=2|ls",
        params=_regime(_donchian(10), when=_adx_ge(25.0), min_regime_bars=2),
    ),
    Candidate(
        key="donchian20",
        label="donchian_20|adx>=25|stop=none|min=2|ls",
        params=_regime(_donchian(20), when=_adx_ge(25.0), min_regime_bars=2),
    ),
    Candidate(
        key="ema1226",
        label="ema_12_26|adx>=20|stop=none|min=2|ls",
        params=_regime(_ema(12, 26), when=_adx_ge(20.0), min_regime_bars=2),
    ),
    Candidate(
        key="donchian10_med",
        label="donchian_10|adx>=25|stop=med|min=2|ls",
        params=_regime(
            {**_donchian(10), **_STOP_MED},
            when=_adx_ge(25.0),
            min_regime_bars=2,
        ),
    ),
]


def _print_metrics(label: str, report: Any, *, bh: Decimal | None) -> None:
    m = report.metrics
    sharpe = f"{m.sharpe_daily:.2f}" if m.sharpe_daily is not None else "n/a"
    win = f"{m.win_rate * 100:.1f}%" if m.win_rate is not None else "n/a"
    pf = f"{m.profit_factor:.2f}" if m.profit_factor is not None else "n/a"
    bh_s = f"{float(bh):+.2f}%" if bh is not None else "n/a"
    vs = ""
    if bh is not None:
        vs = f"  vs_bh={float(m.total_return_pct) - float(bh):+.2f}%"
    print(f"=== {label} ===")
    print(
        f"return={float(m.total_return_pct):+.2f}%  maxdd={float(m.max_drawdown_pct):.2f}%  "
        f"sharpe={sharpe}  trips={m.round_trip_count}  win={win}  pf={pf}  bh={bh_s}{vs}"
    )
    if report.flags:
        for flag in report.flags:
            print(f"  ! {flag.flag.value}: {flag.message}")
    print()


def _validate_one(candidate: Candidate, *, discord: bool) -> str:
    settings = Settings()
    base = load_config(overlay=_OVERLAY)
    config = base.model_copy(
        update={
            "strategy": StrategyConfig(path=_REGIME_STRATEGY_PATH, params={}),
            "trading": base.trading.model_copy(update={"timeframe": _TIMEFRAME}),
        }
    )
    container = build_container(settings, config)
    repo = ParquetMarketDataRepository(Path(settings.data_dir), exchange=config.trading.exchange)
    bars = list(repo.load_bars(_SYMBOL, _TIMEFRAME))
    if not bars:
        raise SystemExit(f"No {_TIMEFRAME} bars for {_SYMBOL}")

    rules = InstrumentRulesCache(Path(settings.data_dir), max_age_hours=24 * 30).load(
        config.trading.exchange, _SYMBOL
    )
    if rules is None:
        raise SystemExit(f"No instrument rules for {_SYMBOL}")

    span_days = (bars[-1].timestamp - bars[0].timestamp) / timedelta(days=1)
    print(
        f"\n########## {candidate.key} ##########\n"
        f"Validating {candidate.label} on {_SYMBOL}@{_TIMEFRAME}: {len(bars)} bars "
        f"({bars[0].timestamp.date()} -> {bars[-1].timestamp.date()}, ~{span_days:.0f}d)"
    )
    print(
        f"(session entries[{_ENTRY_START},{_ENTRY_END}) flatten>={_FLATTEN}; "
        f"spread_bps={config.backtest.spread_bps}; funding not modelled)\n"
    )

    run = build_backtest_engine(
        container,
        rules,
        symbol=_SYMBOL,
        timeframe=_TIMEFRAME,
        strategy_params=candidate.params,
    )
    try:
        full_result = run.engine.run(bars, _TIMEFRAME)
    finally:
        run.teardown()
    full_report = build_performance_report(
        full_result,
        bars,
        min_round_trips=config.analytics.min_round_trips,
        min_bars=config.analytics.min_bars,
        min_daily_returns_for_sharpe=config.analytics.min_daily_returns_for_sharpe,
        bootstrap_iterations=config.analytics.bootstrap_iterations,
        bootstrap_seed=config.analytics.bootstrap_seed,
        market_sma_period=config.analytics.market_sma_period,
    )
    _print_metrics("Full history", full_report, bh=full_report.buy_and_hold_return_pct)

    trips = reconstruct_round_trips(full_result.fills)
    if trips:
        hours = [(t.exit_time - t.entry_time).total_seconds() / 3600 for t in trips]
        overnightish = sum(1 for h in hours if h >= 12)
        past_flatten = sum(1 for t in trips if t.exit_time.hour >= _FLATTEN)
        print(
            f"Hold stats: n={len(hours)}  median={sorted(hours)[len(hours) // 2]:.1f}h  "
            f"mean={sum(hours) / len(hours):.1f}h  >=12h={overnightish}/{len(hours)}  "
            f"exit_hour>={_FLATTEN}={past_flatten}/{len(hours)}"
        )
        print()

    windows = iter_walk_forward_windows(
        len(bars), is_bars=_IS_BARS, oos_bars=_OOS_BARS, step_bars=_STEP_BARS
    )
    print(
        f"Rolling OOS (fixed params): {len(windows)} folds  "
        f"IS_spacer={_IS_BARS} OOS={_OOS_BARS} step={_STEP_BARS}"
    )
    print(f"\n{'fold':>4} {'OOS window':>24} {'oos_ret%':>9} {'oos_trips':>9} {'bh%':>8}")
    print("-" * 60)

    oos_results = []
    oos_returns: list[float] = []
    for fold_index, (_is_s, _is_e, oos_s, oos_e) in enumerate(windows):
        oos_bars = bars[oos_s:oos_e]
        run = build_backtest_engine(
            container,
            rules,
            symbol=_SYMBOL,
            timeframe=_TIMEFRAME,
            strategy_params=candidate.params,
        )
        try:
            oos_result = run.engine.run(oos_bars, _TIMEFRAME)
        finally:
            run.teardown()
        oos_results.append(oos_result)
        report = build_performance_report(
            oos_result,
            oos_bars,
            min_round_trips=1,
            min_bars=1,
            min_daily_returns_for_sharpe=1,
            bootstrap_iterations=1,
            bootstrap_seed=config.analytics.bootstrap_seed,
            market_sma_period=config.analytics.market_sma_period,
        )
        ret = float(report.metrics.total_return_pct)
        oos_returns.append(ret)
        bh = (
            float(report.buy_and_hold_return_pct)
            if report.buy_and_hold_return_pct is not None
            else float("nan")
        )
        a = oos_bars[0].timestamp.date()
        b = oos_bars[-1].timestamp.date()
        print(
            f"{fold_index:>4} {str(a)}->{b}  {ret:>+9.2f} "
            f"{report.metrics.round_trip_count:>9} {bh:>+8.2f}",
            flush=True,
        )

    stitched_ret: float | None = None
    oos_summary = "OOS folds: n/a"
    if oos_returns:
        wins = sum(1 for r in oos_returns if r > 0)
        oos_summary = (
            f"OOS folds: {len(oos_returns)}  positive={wins}/{len(oos_returns)}  "
            f"mean={sum(oos_returns) / len(oos_returns):+.2f}%  "
            f"median={sorted(oos_returns)[len(oos_returns) // 2]:+.2f}%"
        )
        print(f"\n{oos_summary}")
    stitched = stitch_oos_equity(oos_results, starting_cash=config.backtest.starting_cash)
    if stitched:
        start_eq = stitched[0].equity
        end_eq = stitched[-1].equity
        stitched_ret = float((end_eq - start_eq) / start_eq * 100) if start_eq else 0.0
        print(f"Stitched OOS equity return: {stitched_ret:+.2f}%")

    print(
        "\nVerdict rule: do not promote to ig-eurusd.yaml unless OOS is consistently "
        "positive and preferably competitive with buy-and-hold."
    )

    m = full_report.metrics
    bh = full_report.buy_and_hold_return_pct
    bh_s = f"bh={float(bh):+.2f}%" if bh is not None else "bh=n/a"
    vs = f" vs_bh={float(m.total_return_pct) - float(bh):+.2f}%" if bh is not None else ""
    summary_lines = [
        f"Validation done: session_breakout {candidate.key} {_SYMBOL}@{_TIMEFRAME}",
        f"label={candidate.label}",
        (
            f"full: ret={float(m.total_return_pct):+.2f}% "
            f"maxdd={float(m.max_drawdown_pct):.2f}% trips={m.round_trip_count} "
            f"{bh_s}{vs}"
        ),
        oos_summary,
    ]
    if stitched_ret is not None:
        summary_lines.append(f"stitched OOS: {stitched_ret:+.2f}%")
    summary = "\n".join(summary_lines)
    if notify_demo_research(summary, enabled=discord):
        print("Posted summary to Discord demo webhook.", flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        choices=("donchian10", "donchian20", "ema1226", "donchian10_med"),
        default=None,
    )
    parser.add_argument("--no-discord", action="store_true")
    args = parser.parse_args()

    chosen = [c for c in _CANDIDATES if args.only is None or c.key == args.only]
    if not chosen:
        raise SystemExit("No candidates selected.")

    for candidate in chosen:
        _validate_one(candidate, discord=not args.no_discord)


if __name__ == "__main__":
    main()
