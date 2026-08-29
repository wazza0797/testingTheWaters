from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from random import Random

from trading_platform.analytics.metrics import PerformanceMetrics
from trading_platform.analytics.trades import RoundTrip


class SignificanceFlag(StrEnum):
    LOW_SAMPLE_SIZE = "LOW_SAMPLE_SIZE"
    LOW_BAR_COUNT = "LOW_BAR_COUNT"
    WIDE_BOOTSTRAP_CI = "WIDE_BOOTSTRAP_CI"
    INSUFFICIENT_HISTORY_FOR_SHARPE = "INSUFFICIENT_HISTORY_FOR_SHARPE"


@dataclass(frozen=True, slots=True)
class FlagMessage:
    """A significance flag plus the user-facing explanation."""

    flag: SignificanceFlag
    message: str


@dataclass(frozen=True, slots=True)
class BootstrapCI:
    """Percentile confidence interval on total round-trip PnL (absolute currency)."""

    lower: Decimal
    upper: Decimal
    iterations: int
    seed: int

    @property
    def spans_zero(self) -> bool:
        return self.lower <= 0 <= self.upper


def bootstrap_return_ci(
    round_trips: Sequence[RoundTrip],
    *,
    iterations: int = 1000,
    seed: int = 42,
    confidence: float = 0.95,
) -> BootstrapCI | None:
    """Resample round-trip PnLs with replacement; return a percentile CI.

    Uses absolute PnL sum (not percentage return) so the CI is well-defined
    even without a starting-cash reference. Returns `None` when there are no
    round-trips to resample.
    """
    if not round_trips:
        return None
    if iterations < 1:
        raise ValueError(f"iterations must be >= 1, got {iterations}")
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")

    pnls = [float(t.pnl) for t in round_trips]
    n = len(pnls)
    rng = Random(seed)
    samples: list[float] = []
    for _ in range(iterations):
        total = 0.0
        for _ in range(n):
            total += pnls[rng.randrange(n)]
        samples.append(total)
    samples.sort()

    alpha = 1.0 - confidence
    lower_idx = int(alpha / 2 * iterations)
    upper_idx = int((1.0 - alpha / 2) * iterations) - 1
    lower_idx = max(0, min(lower_idx, iterations - 1))
    upper_idx = max(0, min(upper_idx, iterations - 1))
    return BootstrapCI(
        lower=Decimal(str(samples[lower_idx])),
        upper=Decimal(str(samples[upper_idx])),
        iterations=iterations,
        seed=seed,
    )


def compute_flags(
    metrics: PerformanceMetrics,
    round_trips: Sequence[RoundTrip],
    *,
    min_round_trips: int = 30,
    min_bars: int = 500,
    min_daily_returns_for_sharpe: int = 30,
    bootstrap_iterations: int = 1000,
    bootstrap_seed: int = 42,
) -> tuple[tuple[FlagMessage, ...], BootstrapCI | None]:
    """Compute pragmatic significance flags for a completed run."""
    flags: list[FlagMessage] = []
    ci = bootstrap_return_ci(round_trips, iterations=bootstrap_iterations, seed=bootstrap_seed)

    if metrics.round_trip_count < min_round_trips:
        flags.append(
            FlagMessage(
                flag=SignificanceFlag.LOW_SAMPLE_SIZE,
                message=(
                    f"Only {metrics.round_trip_count} round-trips — result may be "
                    f"luck, not edge (recommend ≥{min_round_trips})"
                ),
            )
        )

    if metrics.bars_processed < min_bars:
        flags.append(
            FlagMessage(
                flag=SignificanceFlag.LOW_BAR_COUNT,
                message=(
                    f"Short history ({metrics.bars_processed} bars) — metrics may "
                    f"not be representative (recommend ≥{min_bars})"
                ),
            )
        )

    if metrics.daily_return_count < min_daily_returns_for_sharpe:
        flags.append(
            FlagMessage(
                flag=SignificanceFlag.INSUFFICIENT_HISTORY_FOR_SHARPE,
                message=(
                    f"Only {metrics.daily_return_count} daily return observations "
                    f"— Sharpe is informational only (recommend ≥{min_daily_returns_for_sharpe})"
                ),
            )
        )

    if ci is not None and ci.spans_zero and metrics.round_trip_count > 0:
        flags.append(
            FlagMessage(
                flag=SignificanceFlag.WIDE_BOOTSTRAP_CI,
                message=(
                    "Return not statistically distinguishable from zero at 95% "
                    f"(bootstrap CI [{ci.lower:.4f}, {ci.upper:.4f}])"
                ),
            )
        )

    return tuple(flags), ci
