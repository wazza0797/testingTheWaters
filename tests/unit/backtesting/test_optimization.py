from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from trading_platform.backtesting.optimization import (
    GridSearchOptimizer,
    ParamGrid,
    score_result,
)
from trading_platform.backtesting.result import BacktestResult, EquityPoint
from trading_platform.domain.errors import ConfigurationError


def _result(*, return_pct: str, sharpe_equity_days: int | None = None) -> BacktestResult:
    start = Decimal("10000")
    end = start * (Decimal("1") + Decimal(return_pct) / Decimal("100"))
    curve: list[EquityPoint] = []
    if sharpe_equity_days is not None:
        base = datetime(2024, 1, 1, tzinfo=UTC)
        # Steady climb so Sharpe is defined and positive when return_pct > 0
        for i in range(sharpe_equity_days):
            frac = Decimal(i) / Decimal(max(sharpe_equity_days - 1, 1))
            eq = start + (end - start) * frac
            curve.append(EquityPoint(base + timedelta(days=i), eq))
    else:
        curve = [
            EquityPoint(datetime(2024, 1, 1, tzinfo=UTC), start),
            EquityPoint(datetime(2024, 1, 2, tzinfo=UTC), end),
        ]
    return BacktestResult(
        symbol="BTC/USDT",
        timeframe="1h",
        starting_cash=start,
        ending_cash=end,
        bars_processed=len(curve),
        fills=(),
        total_fees_paid=Decimal("0"),
        equity_curve=tuple(curve),
        final_position=None,
    )


class FakeRun:
    def __init__(self, result: BacktestResult) -> None:
        self.engine = FakeEngine(result)
        self.teardown_calls = 0

    def teardown(self) -> None:
        self.teardown_calls += 1


class FakeEngine:
    def __init__(self, result: BacktestResult) -> None:
        self._result = result

    def run(self, bars: Any, timeframe: str) -> BacktestResult:
        return self._result


class TestParamGrid:
    def test_cartesian_product(self) -> None:
        grid = ParamGrid({"a": [1, 2], "b": ["x", "y"]})
        combos = list(grid)
        assert len(grid) == 4
        assert combos == [
            {"a": 1, "b": "x"},
            {"a": 1, "b": "y"},
            {"a": 2, "b": "x"},
            {"a": 2, "b": "y"},
        ]

    def test_empty_grid_raises(self) -> None:
        with pytest.raises(ConfigurationError, match="at least one"):
            ParamGrid({})

    def test_empty_axis_raises(self) -> None:
        with pytest.raises(ConfigurationError, match="non-empty"):
            ParamGrid({"a": []})


class TestScoreResult:
    def test_total_return_objective(self) -> None:
        assert score_result(_result(return_pct="12.5"), "total_return_pct") == 12.5

    def test_missing_sharpe_is_worst(self) -> None:
        # Single equity point → no daily returns → Sharpe None
        flat = BacktestResult(
            symbol="BTC/USDT",
            timeframe="1h",
            starting_cash=Decimal("10000"),
            ending_cash=Decimal("10000"),
            bars_processed=1,
            fills=(),
            total_fees_paid=Decimal("0"),
            equity_curve=(EquityPoint(datetime(2024, 1, 1, tzinfo=UTC), Decimal("10000")),),
            final_position=None,
        )
        assert score_result(flat, "sharpe_daily") == float("-inf")


class TestGridSearchOptimizer:
    def test_picks_best_params_by_total_return(self, make_bar) -> None:
        bars = [
            make_bar(timestamp=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=i))
            for i in range(3)
        ]
        # fast=10 clearly wins
        results_by_params = {
            (5,): _result(return_pct="1"),
            (10,): _result(return_pct="50"),
            (15,): _result(return_pct="2"),
        }
        runs: list[FakeRun] = []

        def factory(params: Any) -> FakeRun:
            key = (params["fast_period"],)
            run = FakeRun(results_by_params[key])
            runs.append(run)
            return run

        opt = GridSearchOptimizer(factory, objective="total_return_pct")
        out = opt.run(bars, "1h", ParamGrid({"fast_period": [5, 10, 15]}))

        assert out.best_params == {"fast_period": 10}
        assert out.best_score == 50.0
        assert len(out.trials) == 3
        assert all(r.teardown_calls == 1 for r in runs)

    def test_empty_bars_raises(self) -> None:
        opt = GridSearchOptimizer(lambda _p: FakeRun(_result(return_pct="0")))
        with pytest.raises(ConfigurationError, match="non-empty"):
            opt.run([], "1h", ParamGrid({"a": [1]}))
