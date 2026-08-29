from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from itertools import product
from typing import Any, Literal

from trading_platform.analytics.metrics import compute_metrics
from trading_platform.backtesting.result import BacktestResult
from trading_platform.container import BacktestRun
from trading_platform.domain.errors import ConfigurationError
from trading_platform.domain.models.bar import Bar

ObjectiveName = Literal["total_return_pct", "sharpe_daily"]


@dataclass(frozen=True, slots=True)
class ParamGrid:
    """Cartesian product over strategy parameter axes."""

    axes: Mapping[str, Sequence[Any]]

    def __post_init__(self) -> None:
        if not self.axes:
            raise ConfigurationError("param_grid must contain at least one parameter axis")
        for key, values in self.axes.items():
            if not values:
                raise ConfigurationError(
                    f"param_grid[{key!r}] must be a non-empty list of candidate values"
                )

    def __iter__(self) -> Iterator[dict[str, Any]]:
        keys = list(self.axes.keys())
        for combo in product(*(self.axes[k] for k in keys)):
            yield dict(zip(keys, combo, strict=True))

    def __len__(self) -> int:
        n = 1
        for values in self.axes.values():
            n *= len(values)
        return n


@dataclass(frozen=True, slots=True)
class GridSearchTrial:
    params: dict[str, Any]
    score: float
    result: BacktestResult


@dataclass(frozen=True, slots=True)
class GridSearchResult:
    best_params: dict[str, Any]
    best_score: float
    best_result: BacktestResult
    trials: tuple[GridSearchTrial, ...]


def score_result(result: BacktestResult, objective: ObjectiveName) -> float:
    """Higher is better. Missing Sharpe is treated as −inf (never wins)."""
    if objective == "total_return_pct":
        return float(result.total_return_pct)
    metrics = compute_metrics(
        result.fills,
        result.equity_curve,
        result.starting_cash,
        bars_processed=result.bars_processed,
    )
    if metrics.sharpe_daily is None:
        return float("-inf")
    return metrics.sharpe_daily


class GridSearchOptimizer:
    """Exhaustive grid search over `ParamGrid` on a fixed IS bar window.

    Each candidate gets a fresh `BacktestRun` from `engine_factory(params)` and
    is torn down before the next trial — same isolation as hold-out windows.
    Tie-break: first highest score in iteration order (deterministic).
    """

    def __init__(
        self,
        engine_factory: Callable[[Mapping[str, Any]], BacktestRun],
        *,
        objective: ObjectiveName = "sharpe_daily",
    ) -> None:
        self._engine_factory = engine_factory
        self._objective = objective

    def run(
        self,
        bars: Sequence[Bar],
        timeframe: str,
        grid: ParamGrid,
    ) -> GridSearchResult:
        if not bars:
            raise ConfigurationError("grid search requires a non-empty in-sample bar window")

        trials: list[GridSearchTrial] = []
        best: GridSearchTrial | None = None

        for params in grid:
            result = self._run_once(bars, timeframe, params)
            score = score_result(result, self._objective)
            trial = GridSearchTrial(params=params, score=score, result=result)
            trials.append(trial)
            if best is None or score > best.score:
                best = trial

        assert best is not None  # grid is non-empty by construction
        return GridSearchResult(
            best_params=best.params,
            best_score=best.score,
            best_result=best.result,
            trials=tuple(trials),
        )

    def _run_once(
        self,
        bars: Sequence[Bar],
        timeframe: str,
        params: Mapping[str, Any],
    ) -> BacktestResult:
        run = self._engine_factory(params)
        try:
            return run.engine.run(bars, timeframe)
        finally:
            run.teardown()
