from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from trading_platform.backtesting.optimization import (
    GridSearchOptimizer,
    GridSearchResult,
    ObjectiveName,
    ParamGrid,
)
from trading_platform.backtesting.result import BacktestResult, EquityPoint
from trading_platform.container import BacktestRun
from trading_platform.domain.errors import ConfigurationError, MarketDataError
from trading_platform.domain.models.bar import Bar


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    """One rolling window: IS grid search + OOS evaluation with frozen params."""

    fold_index: int
    is_start_index: int
    is_end_index: int  # exclusive
    oos_start_index: int
    oos_end_index: int  # exclusive
    best_params: dict[str, Any]
    is_search: GridSearchResult
    oos_result: BacktestResult


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    """All folds plus an OOS-only stitched equity curve (never includes IS)."""

    folds: tuple[WalkForwardFold, ...]
    stitched_oos_equity: tuple[EquityPoint, ...]
    objective: ObjectiveName

    @property
    def fold_count(self) -> int:
        return len(self.folds)


def iter_walk_forward_windows(
    n_bars: int,
    *,
    is_bars: int,
    oos_bars: int,
    step_bars: int,
) -> list[tuple[int, int, int, int]]:
    """Return `(is_start, is_end, oos_start, oos_end)` index tuples (end exclusive)."""
    if is_bars < 1 or oos_bars < 1 or step_bars < 1:
        raise ConfigurationError("is_bars, oos_bars, and step_bars must each be >= 1")
    windows: list[tuple[int, int, int, int]] = []
    start = 0
    while start + is_bars + oos_bars <= n_bars:
        is_start = start
        is_end = start + is_bars
        oos_start = is_end
        oos_end = is_end + oos_bars
        windows.append((is_start, is_end, oos_start, oos_end))
        start += step_bars
    return windows


def stitch_oos_equity(
    oos_results: Sequence[BacktestResult],
    *,
    starting_cash: Decimal,
) -> tuple[EquityPoint, ...]:
    """Compound successive OOS equity curves onto one continuous path.

    Each fold's backtest restarts at `starting_cash`; we rebase each segment's
    relative path onto the running level so fold boundaries don't reset.
    """
    if not oos_results:
        return ()
    stitched: list[EquityPoint] = []
    level = starting_cash
    for result in oos_results:
        curve = result.equity_curve
        if not curve:
            continue
        base = curve[0].equity
        for point in curve:
            equity = level if base == 0 else level * (point.equity / base)
            stitched.append(EquityPoint(timestamp=point.timestamp, equity=equity))
        level = stitched[-1].equity if stitched else level
    return tuple(stitched)


class WalkForwardRunner:
    """Slide fixed-size IS/OOS windows; grid-search on IS; evaluate on OOS only."""

    def __init__(
        self,
        engine_factory: Callable[[Mapping[str, Any]], BacktestRun],
        *,
        is_bars: int,
        oos_bars: int,
        step_bars: int,
        param_grid: Mapping[str, Sequence[Any]],
        objective: ObjectiveName = "sharpe_daily",
        starting_cash: Decimal = Decimal("10000"),
    ) -> None:
        self._engine_factory = engine_factory
        self._is_bars = is_bars
        self._oos_bars = oos_bars
        self._step_bars = step_bars
        self._grid = ParamGrid(param_grid)
        self._objective = objective
        self._starting_cash = starting_cash
        self._optimizer = GridSearchOptimizer(engine_factory, objective=objective)

    def run(self, bars: Sequence[Bar], timeframe: str) -> WalkForwardResult:
        windows = iter_walk_forward_windows(
            len(bars),
            is_bars=self._is_bars,
            oos_bars=self._oos_bars,
            step_bars=self._step_bars,
        )
        if not windows:
            raise MarketDataError(
                f"not enough bars for walk-forward: need at least "
                f"{self._is_bars + self._oos_bars}, got {len(bars)}"
            )

        folds: list[WalkForwardFold] = []
        for fold_index, (is_start, is_end, oos_start, oos_end) in enumerate(windows):
            is_bars = bars[is_start:is_end]
            oos_bars = bars[oos_start:oos_end]
            # Defensive: windows are index-correct; empty would be a programmer error.
            if not is_bars or not oos_bars:
                raise MarketDataError(
                    f"walk-forward fold {fold_index} produced an empty IS or OOS window"
                )

            search = self._optimizer.run(is_bars, timeframe, self._grid)
            oos_result = self._run_oos(oos_bars, timeframe, search.best_params)
            folds.append(
                WalkForwardFold(
                    fold_index=fold_index,
                    is_start_index=is_start,
                    is_end_index=is_end,
                    oos_start_index=oos_start,
                    oos_end_index=oos_end,
                    best_params=search.best_params,
                    is_search=search,
                    oos_result=oos_result,
                )
            )

        stitched = stitch_oos_equity(
            [f.oos_result for f in folds],
            starting_cash=self._starting_cash,
        )
        return WalkForwardResult(
            folds=tuple(folds),
            stitched_oos_equity=stitched,
            objective=self._objective,
        )

    def _run_oos(
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
