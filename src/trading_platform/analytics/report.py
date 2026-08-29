from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from trading_platform.analytics.benchmark import buy_and_hold_return_pct
from trading_platform.analytics.metrics import PerformanceMetrics, compute_metrics
from trading_platform.analytics.regime import RegimePeriodRow, calendar_splits, market_regime_splits
from trading_platform.analytics.significance import (
    BootstrapCI,
    FlagMessage,
    compute_flags,
)
from trading_platform.analytics.trades import RoundTrip, reconstruct_round_trips
from trading_platform.backtesting.result import BacktestResult
from trading_platform.domain.models.bar import Bar


@dataclass(frozen=True, slots=True)
class PerformanceReport:
    """Complete analytics output for one backtest (or paper) window."""

    metrics: PerformanceMetrics
    round_trips: tuple[RoundTrip, ...]
    flags: tuple[FlagMessage, ...]
    bootstrap_ci: BootstrapCI | None
    calendar_quarters: tuple[RegimePeriodRow, ...]
    market_regimes: tuple[RegimePeriodRow, ...]
    buy_and_hold_return_pct: Decimal | None

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable dict (Decimals → str, enums → value)."""
        result = _to_jsonable(asdict(self))
        assert isinstance(result, dict)
        return result


def build_performance_report(
    result: BacktestResult,
    bars: Sequence[Bar] | None = None,
    *,
    min_round_trips: int = 30,
    min_bars: int = 500,
    min_daily_returns_for_sharpe: int = 30,
    bootstrap_iterations: int = 1000,
    bootstrap_seed: int = 42,
    market_sma_period: int = 200,
) -> PerformanceReport:
    """Pure post-run analysis: `BacktestResult` (+ optional bars) → report."""
    trips = reconstruct_round_trips(result.fills)
    metrics = compute_metrics(
        result.fills,
        result.equity_curve,
        result.starting_cash,
        bars_processed=result.bars_processed,
        round_trips=trips,
    )
    flags, ci = compute_flags(
        metrics,
        trips,
        min_round_trips=min_round_trips,
        min_bars=min_bars,
        min_daily_returns_for_sharpe=min_daily_returns_for_sharpe,
        bootstrap_iterations=bootstrap_iterations,
        bootstrap_seed=bootstrap_seed,
    )
    bar_seq = tuple(bars) if bars is not None else ()
    quarters = (
        calendar_splits(result.equity_curve, trips, bar_seq, by="quarter")
        if bar_seq or result.equity_curve
        else ()
    )
    regimes = (
        market_regime_splits(result.equity_curve, trips, bar_seq, sma_period=market_sma_period)
        if bar_seq
        else ()
    )
    bh = buy_and_hold_return_pct(bar_seq) if bar_seq else None
    return PerformanceReport(
        metrics=metrics,
        round_trips=trips,
        flags=flags,
        bootstrap_ci=ci,
        calendar_quarters=quarters,
        market_regimes=regimes,
        buy_and_hold_return_pct=bh,
    )


def _to_jsonable(value: Any) -> Any:
    from datetime import date, datetime
    from enum import Enum
    from math import isinf, isnan

    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, float):
        if isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        if isnan(value):
            return None
        return value
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return value
