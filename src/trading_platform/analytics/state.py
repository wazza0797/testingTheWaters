from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from trading_platform.analytics.metrics import PerformanceMetrics, compute_metrics
from trading_platform.analytics.trades import RoundTrip, reconstruct_round_trips
from trading_platform.backtesting.result import EquityPoint
from trading_platform.domain.models.fill import Fill

_ZERO = Decimal("0")


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
        """Closed-trade metrics only — equity curve is empty (no MTM yet)."""
        trips = self.round_trips
        # Synthetic flat equity so compute_metrics stays well-defined; Sharpe
        # will be None (insufficient daily returns) which is honest for M5.
        equity: tuple[EquityPoint, ...] = ()
        realized = sum((t.pnl for t in trips), _ZERO)
        ending = self.starting_cash + realized
        return compute_metrics(
            self.fills,
            equity,
            self.starting_cash if self.starting_cash != 0 else ending or Decimal("1"),
            bars_processed=0,
            round_trips=trips,
        )
