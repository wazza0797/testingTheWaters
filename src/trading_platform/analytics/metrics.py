from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from math import sqrt

from trading_platform.analytics.trades import RoundTrip, reconstruct_round_trips
from trading_platform.backtesting.result import EquityPoint
from trading_platform.domain.models.fill import Fill

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")
_CRYPTO_ANNUALIZATION = sqrt(365)


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    """Core trading-performance numbers derived from fills + equity curve."""

    starting_cash: Decimal
    ending_equity: Decimal
    total_return_pct: Decimal
    max_drawdown_pct: Decimal
    sharpe_daily: float | None
    round_trip_count: int
    win_count: int
    loss_count: int
    win_rate: float | None
    profit_factor: float | None
    avg_trade_pnl: Decimal | None
    total_fees: Decimal
    bars_processed: int
    daily_return_count: int


def compute_metrics(
    fills: Sequence[Fill],
    equity_curve: Sequence[EquityPoint],
    starting_cash: Decimal,
    *,
    bars_processed: int,
    round_trips: Sequence[RoundTrip] | None = None,
) -> PerformanceMetrics:
    """Pure metrics over a completed run — no event bus, no I/O."""
    trips = tuple(round_trips) if round_trips is not None else reconstruct_round_trips(fills)
    ending_equity = equity_curve[-1].equity if equity_curve else starting_cash
    total_return_pct = (
        _ZERO if starting_cash == 0 else (ending_equity - starting_cash) / starting_cash * _HUNDRED
    )
    wins = [t for t in trips if t.pnl > 0]
    losses = [t for t in trips if t.pnl < 0]
    gross_profit = sum((t.pnl for t in wins), _ZERO)
    gross_loss = sum((t.pnl for t in losses), _ZERO)

    win_rate = len(wins) / len(trips) if trips else None

    profit_factor: float | None
    if gross_loss < 0:
        profit_factor = float(gross_profit / abs(gross_loss))
    elif gross_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = None

    avg_trade_pnl = sum((t.pnl for t in trips), _ZERO) / Decimal(len(trips)) if trips else None

    daily_returns = _daily_returns(equity_curve)
    sharpe = _sharpe_ratio(daily_returns)

    return PerformanceMetrics(
        starting_cash=starting_cash,
        ending_equity=ending_equity,
        total_return_pct=total_return_pct,
        max_drawdown_pct=max_drawdown_pct(equity_curve),
        sharpe_daily=sharpe,
        round_trip_count=len(trips),
        win_count=len(wins),
        loss_count=len(losses),
        win_rate=win_rate,
        profit_factor=profit_factor,
        avg_trade_pnl=avg_trade_pnl,
        total_fees=sum((f.fee for f in fills), _ZERO),
        bars_processed=bars_processed,
        daily_return_count=len(daily_returns),
    )


def max_drawdown_pct(equity_curve: Sequence[EquityPoint]) -> Decimal:
    """Peak-to-trough drawdown as a negative percentage (0 if no decline)."""
    if not equity_curve:
        return _ZERO
    peak = equity_curve[0].equity
    worst = _ZERO
    for point in equity_curve:
        if point.equity > peak:
            peak = point.equity
        if peak > 0:
            drawdown = (point.equity - peak) / peak * _HUNDRED
            if drawdown < worst:
                worst = drawdown
    return worst


def _daily_returns(equity_curve: Sequence[EquityPoint]) -> list[float]:
    """Last equity observation per UTC calendar day → simple returns."""
    if len(equity_curve) < 2:
        return []
    by_day: dict[date, Decimal] = {}
    for point in equity_curve:
        by_day[point.timestamp.date()] = point.equity
    ordered = [by_day[d] for d in sorted(by_day)]
    returns: list[float] = []
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if previous == 0:
            continue
        returns.append(float((current - previous) / previous))
    return returns


def _sharpe_ratio(daily_returns: Sequence[float], *, rf: float = 0.0) -> float | None:
    if len(daily_returns) < 2:
        return None
    n = len(daily_returns)
    mean = sum(daily_returns) / n
    variance = sum((r - mean) ** 2 for r in daily_returns) / (n - 1)
    if variance <= 0:
        return None
    return (mean - rf) / sqrt(variance) * _CRYPTO_ANNUALIZATION
