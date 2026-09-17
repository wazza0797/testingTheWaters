#!/usr/bin/env python3
"""GBPEUR low-frequency research: regime-gated recipes on 1h / 4h.

Hypothesis from the earlier 15m/1h indicator matrix: frequent entries lose to
IG's GBP/EUR spread. This runner trades *less* — `RegimeRouterStrategy` with
`default: flat`, so playbooks only fire inside a trending regime (ADX gate);
chop stays flat (and any open position is safety-net flattened on regime exit).

Overnight funding is intentionally **not** modelled yet: these recipes use
indicator exits / optional wide ATR stops and may still hold past 22:00
London. We revisit funding once a candidate deliberately wants overnight
holds; until then, treat multi-day PnL as indicative only.

Usage:
    uv run python scripts/research_gbpeur_recipes.py
    uv run python scripts/research_gbpeur_recipes.py --timeframes 4h
    uv run python scripts/research_gbpeur_recipes.py --recipes trend_ema,trend_donchian
"""

from __future__ import annotations

import argparse
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

# Wide ATR risk only — tight stops chopped on lower TFs. "none" = indicator exit.
STOP_GRIDS: dict[str, dict[str, float | int]] = {
    "none": {},
    "wide": {
        "long_stop_atr": 3.0,
        "long_take_profit_atr": 5.0,
        "short_stop_atr": 3.0,
        "short_take_profit_atr": 5.0,
        "stop_atr_period": 14,
    },
}

_ADX_TREND = {"compare": {"indicator": "adx", "period": 14, "op": ">=", "value": 25}}
_ADX_STRONG = {"compare": {"indicator": "adx", "period": 14, "op": ">=", "value": 30}}


def _trend_regime(name: str, when: dict[str, Any], playbook: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "when": when, "playbook": playbook}


_EMA_PLAYBOOK = {
    "long_entry": {
        "cross": {
            "left": {"indicator": "ema", "period": 12},
            "right": {"indicator": "ema", "period": 26},
            "direction": "above",
        }
    },
    "long_exit": {
        "cross": {
            "left": {"indicator": "ema", "period": 12},
            "right": {"indicator": "ema", "period": 26},
            "direction": "below",
        }
    },
    "short_entry": {
        "cross": {
            "left": {"indicator": "ema", "period": 12},
            "right": {"indicator": "ema", "period": 26},
            "direction": "below",
        }
    },
    "short_exit": {
        "cross": {
            "left": {"indicator": "ema", "period": 12},
            "right": {"indicator": "ema", "period": 26},
            "direction": "above",
        }
    },
}

_DONCHIAN_PLAYBOOK = {
    "long_entry": {
        "compare_indicators": {
            "left": "close",
            "right": {"indicator": "donchian_upper", "period": 20},
            "op": ">=",
        }
    },
    "long_exit": {
        "compare_indicators": {
            "left": "close",
            "right": {"indicator": "donchian_mid", "period": 20},
            "op": "<=",
        }
    },
    "short_entry": {
        "compare_indicators": {
            "left": "close",
            "right": {"indicator": "donchian_lower", "period": 20},
            "op": "<=",
        }
    },
    "short_exit": {
        "compare_indicators": {
            "left": "close",
            "right": {"indicator": "donchian_mid", "period": 20},
            "op": ">=",
        }
    },
}

_SUPERTREND_PLAYBOOK = {
    "long_entry": {
        "cross": {
            "left": {"indicator": "supertrend_dir", "period": 10, "multiplier": 3.0},
            "right": 0.0,
            "direction": "above",
        }
    },
    "long_exit": {
        "cross": {
            "left": {"indicator": "supertrend_dir", "period": 10, "multiplier": 3.0},
            "right": 0.0,
            "direction": "below",
        }
    },
    "short_entry": {
        "cross": {
            "left": {"indicator": "supertrend_dir", "period": 10, "multiplier": 3.0},
            "right": 0.0,
            "direction": "below",
        }
    },
    "short_exit": {
        "cross": {
            "left": {"indicator": "supertrend_dir", "period": 10, "multiplier": 3.0},
            "right": 0.0,
            "direction": "above",
        }
    },
}

# Each recipe is RegimeRouterStrategy params (minus lookback / stop merge).
BASE_RECIPES: dict[str, dict[str, Any]] = {
    "trend_ema": {
        "default": "flat",
        "min_regime_bars": 6,
        "regimes": [_trend_regime("trend", _ADX_TREND, _EMA_PLAYBOOK)],
    },
    "trend_ema_strict": {
        "default": "flat",
        "min_regime_bars": 8,
        "regimes": [_trend_regime("strong_trend", _ADX_STRONG, _EMA_PLAYBOOK)],
    },
    "trend_donchian": {
        "default": "flat",
        "min_regime_bars": 6,
        "regimes": [_trend_regime("trend", _ADX_TREND, _DONCHIAN_PLAYBOOK)],
    },
    "trend_supertrend": {
        "default": "flat",
        "min_regime_bars": 6,
        "regimes": [_trend_regime("trend", _ADX_TREND, _SUPERTREND_PLAYBOOK)],
    },
}


def _parse_csv(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _trim_bars(bars: list[Bar], max_bars: int | None) -> list[Bar]:
    if max_bars is None or len(bars) <= max_bars:
        return bars
    return bars[-max_bars:]


def _merge_risk_into_regimes(
    regimes: list[dict[str, Any]], risk: dict[str, float | int]
) -> list[dict[str, Any]]:
    if not risk:
        return regimes
    merged: list[dict[str, Any]] = []
    for regime in regimes:
        playbook = {**regime["playbook"], **risk}
        merged.append({**regime, "playbook": playbook})
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeframes",
        default="1h,4h",
        help="Comma-separated timeframes (default: 1h,4h).",
    )
    parser.add_argument(
        "--stops",
        default="none,wide",
        help=f"Stop grids from {sorted(STOP_GRIDS)} (default: none,wide).",
    )
    parser.add_argument(
        "--recipes",
        default=",".join(BASE_RECIPES),
        help="Comma-separated recipe names (default: all).",
    )
    parser.add_argument(
        "--spread-volatility-k",
        type=float,
        default=None,
        help="Override backtest.spread_volatility_k (default: overlay value).",
    )
    parser.add_argument(
        "--max-bars",
        type=int,
        default=None,
        help="Optional trailing-bar cap per timeframe.",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=120,
        help="Bar window for indicators (default: 120).",
    )
    parser.add_argument(
        "--no-discord",
        action="store_true",
        help="Skip posting the end-of-run summary to DISCORD_DEMO_WEBHOOK_URL.",
    )
    args = parser.parse_args()

    timeframes = _parse_csv(args.timeframes)
    stop_names = _parse_csv(args.stops)
    recipe_names = _parse_csv(args.recipes)
    for name in stop_names:
        if name not in STOP_GRIDS:
            raise SystemExit(f"Unknown stop grid {name!r}. Choose from {sorted(STOP_GRIDS)}")
    for name in recipe_names:
        if name not in BASE_RECIPES:
            raise SystemExit(f"Unknown recipe {name!r}. Choose from {sorted(BASE_RECIPES)}")

    settings = Settings()
    base_config = load_config(overlay="ig-gbpeur")
    updates: dict[str, object] = {
        "strategy": StrategyConfig(path=_REGIME_STRATEGY_PATH, params={}),
    }
    if args.spread_volatility_k is not None:
        updates["backtest"] = base_config.backtest.model_copy(
            update={"spread_volatility_k": args.spread_volatility_k}
        )
    config = base_config.model_copy(update=updates)
    container = build_container(settings, config)

    symbol = config.trading.symbol
    exchange = config.trading.exchange
    repository = ParquetMarketDataRepository(Path(settings.data_dir), exchange=exchange)
    rules_cache = InstrumentRulesCache(Path(settings.data_dir), max_age_hours=24 * 30)
    rules = rules_cache.load(exchange, symbol)
    if rules is None:
        raise SystemExit(
            "No cached instrument rules found — run "
            "`uv run trading-platform download-data --overlay ig-gbpeur --days 1` "
            "once (needs IG demo creds) to refresh data/instruments/ig/."
        )

    print(
        f"(spread_bps={config.backtest.spread_bps}, "
        f"spread_volatility_k={config.backtest.spread_volatility_k}, "
        f"lookback={args.lookback}, strategy=RegimeRouter default=flat)"
    )
    print("NOTE: overnight funding not modelled — multi-day holds are indicative only.")

    header = (
        f"{'tf':<4} {'stop':<6} {'recipe':<20} {'return%':>8} {'maxdd%':>8} "
        f"{'sharpe':>8} {'trips':>6} {'win%':>6} {'pf':>6} {'avg_pnl':>9} {'bh%':>7}"
    )
    print(header)
    print("-" * len(header))

    rows: list[tuple[float, str]] = []

    for timeframe in timeframes:
        bars = _trim_bars(list(repository.load_bars(symbol, timeframe)), args.max_bars)
        if not bars:
            print(
                f"{timeframe:<4} (no bars — run: uv run python scripts/backfill_gbpeur_external.py --timeframe {timeframe})"
            )
            continue
        span_days = (bars[-1].timestamp - bars[0].timestamp) / timedelta(days=1)
        print(
            f"# {timeframe}: {len(bars)} bars "
            f"({bars[0].timestamp.date()} -> {bars[-1].timestamp.date()}, "
            f"~{span_days:.0f}d)"
        )

        for stop_name in stop_names:
            risk = STOP_GRIDS[stop_name]
            for recipe_name in recipe_names:
                base = BASE_RECIPES[recipe_name]
                params: dict[str, Any] = {
                    "default": base["default"],
                    "min_regime_bars": base["min_regime_bars"],
                    "regimes": _merge_risk_into_regimes(list(base["regimes"]), risk),
                    "lookback": args.lookback,
                }
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
                    min_round_trips=config.analytics.min_round_trips,
                    min_bars=config.analytics.min_bars,
                    min_daily_returns_for_sharpe=config.analytics.min_daily_returns_for_sharpe,
                    bootstrap_iterations=config.analytics.bootstrap_iterations,
                    bootstrap_seed=config.analytics.bootstrap_seed,
                    market_sma_period=config.analytics.market_sma_period,
                )
                m = report.metrics
                sharpe = f"{m.sharpe_daily:.2f}" if m.sharpe_daily is not None else "n/a"
                win_rate = f"{m.win_rate * 100:.1f}" if m.win_rate is not None else "n/a"
                pf = f"{m.profit_factor:.2f}" if m.profit_factor is not None else "n/a"
                avg_pnl = f"{float(m.avg_trade_pnl):.2f}" if m.avg_trade_pnl is not None else "n/a"
                bh = (
                    f"{float(report.buy_and_hold_return_pct):.2f}"
                    if report.buy_and_hold_return_pct is not None
                    else "n/a"
                )
                line = (
                    f"{timeframe:<4} {stop_name:<6} {recipe_name:<20} "
                    f"{float(m.total_return_pct):>8.2f} {float(m.max_drawdown_pct):>8.2f} "
                    f"{sharpe:>8} {m.round_trip_count:>6} {win_rate:>6} {pf:>6} "
                    f"{avg_pnl:>9} {bh:>7}"
                )
                print(line, flush=True)
                rows.append((float(m.total_return_pct), line))

    if rows:
        print("\n=== Top 5 by total return ===")
        top = sorted(rows, key=lambda r: r[0], reverse=True)[:5]
        for _, line in top:
            print(line)
        summary = "\n".join(
            [
                "Research done: gbpeur_recipes",
                f"tfs={','.join(timeframes)} recipes={','.join(recipe_names)}",
                "Top 5:",
                *[line for _, line in top],
            ]
        )
        if notify_demo_research(summary, enabled=not args.no_discord):
            print("Posted summary to Discord demo webhook.", flush=True)


if __name__ == "__main__":
    main()
