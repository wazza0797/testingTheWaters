from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_platform.analytics.metrics import PerformanceMetrics, compute_metrics
from trading_platform.analytics.trades import RoundTrip, reconstruct_round_trips
from trading_platform.backtesting.result import EquityPoint
from trading_platform.domain.models.fill import Fill

_ZERO = Decimal("0")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass
class RunningPerformanceState:
    """Incremental fill log for paper/live analytics (closed-trade metrics).

    Open-position mark-to-market is intentionally excluded until M6's
    `PortfolioHandler` owns positions — see Milestone 5 design §6.
    """

    fills: list[Fill] = field(default_factory=list)
    signals_rejected_total: int = 0
    starting_cash: Decimal = _ZERO

    def record_fill(self, fill: Fill) -> None:
        self.fills.append(fill)

    def record_rejection(self) -> None:
        self.signals_rejected_total += 1

    @property
    def round_trips(self) -> tuple[RoundTrip, ...]:
        return reconstruct_round_trips(self.fills)

    def snapshot_metrics(self) -> PerformanceMetrics:
        """Closed-trade metrics: equity steps from realized round-trip PnL only.

        No open-position MTM yet — Sharpe stays weak/None without a real
        mark-to-market curve, but `ending_equity` / `total_return_pct` /
        max drawdown reflect the closed-trade path.
        """
        trips = self.round_trips
        starting = self.starting_cash
        equity = _closed_trade_equity_curve(starting, trips)
        return compute_metrics(
            self.fills,
            equity,
            starting,
            bars_processed=0,
            round_trips=trips,
        )


def _closed_trade_equity_curve(
    starting_cash: Decimal,
    trips: tuple[RoundTrip, ...],
) -> tuple[EquityPoint, ...]:
    """Step equity forward by each round-trip's PnL at its exit time."""
    if not trips:
        return (EquityPoint(timestamp=_EPOCH, equity=starting_cash),)

    points: list[EquityPoint] = []
    running = starting_cash
    # Anchor before the first exit so compute_metrics has a start level.
    first_exit = trips[0].exit_time
    points.append(EquityPoint(timestamp=first_exit - timedelta(microseconds=1), equity=running))
    for trip in trips:
        running += trip.pnl
        points.append(EquityPoint(timestamp=trip.exit_time, equity=running))
    return tuple(points)
