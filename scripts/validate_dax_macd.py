#!/usr/bin/env python3
"""Validate the DAX 1h regime-gated MACD candidate from the quick sweep.

Candidate (screened on trailing ~2500 bars):
  macd 12/26/9, ADX>=25, min_regime_bars=4, stop=none

Checks:
1. Full-history backtest (all available Yahoo ^GDAXI stand-in bars).
2. Fixed-param rolling OOS (no IS grid) — does *this* candidate generalize?

Overnight funding / dividend adjustments are **not** modelled.

Usage:
    uv run python scripts/validate_dax_macd.py
"""

from __future__ import annotations

import argparse
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
_OVERLAY = "ig-dax"
_SYMBOL = "IX.D.DAX.DAILY.IP"
_TIMEFRAME = "1h"
_LOOKBACK = 220

_ADX = 25
_MIN_REGIME_BARS = 4
_MACD_FAST = 12
_MACD_SLOW = 26
_MACD_SIGNAL = 9

# Equity-index 1h (~8–10 bars/day): ~5–6 months IS spacer, ~2 months OOS.
_IS_BARS = 1200
_OOS_BARS = 400
_STEP_BARS = 800


def _macd_playbook() -> dict[str, Any]:
    left = {
        "indicator": "macd",
        "fast_period": _MACD_FAST,
        "slow_period": _MACD_SLOW,
        "signal_period": _MACD_SIGNAL,
    }
    right = {
        "indicator": "macd_signal",
        "fast_period": _MACD_FAST,
        "slow_period": _MACD_SLOW,
        "signal_period": _MACD_SIGNAL,
    }
    return {
        "long_entry": {"cross": {"left": left, "right": right, "direction": "above"}},
        "long_exit": {"cross": {"left": left, "right": right, "direction": "below"}},
        "short_entry": {"cross": {"left": left, "right": right, "direction": "below"}},
        "short_exit": {"cross": {"left": left, "right": right, "direction": "above"}},
    }


def _regime_params() -> dict[str, Any]:
    return {
        "default": "flat",
        "min_regime_bars": _MIN_REGIME_BARS,
        "lookback": _LOOKBACK,
        "regimes": [
            {
                "name": "trend",
                "when": {
                    "compare": {
                        "indicator": "adx",
                        "period": 14,
                        "op": ">=",
                        "value": _ADX,
                    }
                },
                "playbook": _macd_playbook(),
            }
        ],
    }


def _print_metrics(label: str, report: Any, *, bh: Decimal | None) -> None:
    m = report.metrics
    sharpe = f"{m.sharpe_daily:.2f}" if m.sharpe_daily is not None else "n/a"
    win = f"{m.win_rate * 100:.1f}%" if m.win_rate is not None else "n/a"
    pf = f"{m.profit_factor:.2f}" if m.profit_factor is not None else "n/a"
    bh_s = f"{float(bh):+.2f}%" if bh is not None else "n/a"
    print(f"=== {label} ===")
    print(
        f"return={float(m.total_return_pct):+.2f}%  maxdd={float(m.max_drawdown_pct):.2f}%  "
        f"sharpe={sharpe}  trips={m.round_trip_count}  win={win}  pf={pf}  bh={bh_s}"
    )
    if report.flags:
        for flag in report.flags:
            print(f"  ! {flag.flag.value}: {flag.message}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-discord",
        action="store_true",
        help="Skip posting the end-of-run summary to DISCORD_DEMO_WEBHOOK_URL.",
    )
    args = parser.parse_args()

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
        raise SystemExit(
            "No 1h bars — run: uv run python scripts/backfill_ig_external.py "
            f"--yahoo '^GDAXI' --epic {_SYMBOL} --timeframe 1h"
        )

    rules = InstrumentRulesCache(Path(settings.data_dir), max_age_hours=24 * 30).load(
        config.trading.exchange, _SYMBOL
    )
    if rules is None:
        raise SystemExit(
            f"No instrument rules for {_SYMBOL} — "
            f"`uv run trading-platform download-data --overlay {_OVERLAY} --days 1`"
        )

    span_days = (bars[-1].timestamp - bars[0].timestamp) / timedelta(days=1)
    params = _regime_params()
    print(
        f"Validating macd_{_MACD_FAST}_{_MACD_SLOW}_{_MACD_SIGNAL} on "
        f"{_SYMBOL}@{_TIMEFRAME}: {len(bars)} bars "
        f"({bars[0].timestamp.date()} -> {bars[-1].timestamp.date()}, ~{span_days:.0f}d)"
    )
    print(
        f"(ADX>={_ADX}, min_regime_bars={_MIN_REGIME_BARS}, stop=none, "
        f"spread_bps={config.backtest.spread_bps}, "
        f"starting_cash={config.backtest.starting_cash}; "
        "overnight funding not modelled)\n"
    )

    run = build_backtest_engine(
        container, rules, symbol=_SYMBOL, timeframe=_TIMEFRAME, strategy_params=params
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
        print(
            f"Hold stats: n={len(hours)}  median={sorted(hours)[len(hours) // 2]:.1f}h  "
            f"mean={sum(hours) / len(hours):.1f}h  >=12h={overnightish}/{len(hours)}"
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
            container, rules, symbol=_SYMBOL, timeframe=_TIMEFRAME, strategy_params=params
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
        "\nVerdict rule: do not promote to ig-dax.yaml unless OOS is consistently "
        "positive and preferably competitive with buy-and-hold on the same windows."
    )

    m = full_report.metrics
    bh = full_report.buy_and_hold_return_pct
    bh_s = f"bh={float(bh):+.2f}%" if bh is not None else "bh=n/a"
    summary_lines = [
        f"Validation done: dax_macd {_SYMBOL}@{_TIMEFRAME}",
        (
            f"full: ret={float(m.total_return_pct):+.2f}% "
            f"maxdd={float(m.max_drawdown_pct):.2f}% trips={m.round_trip_count} {bh_s}"
        ),
        oos_summary,
    ]
    if stitched_ret is not None:
        summary_lines.append(f"stitched OOS: {stitched_ret:+.2f}%")
    if notify_demo_research("\n".join(summary_lines), enabled=not args.no_discord):
        print("Posted summary to Discord demo webhook.", flush=True)


if __name__ == "__main__":
    main()
