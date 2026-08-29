from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from trading_platform.backtesting.result import BacktestResult, EquityPoint
from trading_platform.backtesting.walk_forward import (
    WalkForwardRunner,
    iter_walk_forward_windows,
    stitch_oos_equity,
)
from trading_platform.domain.errors import MarketDataError


def _result(start_eq: str, end_eq: str, *, day0: int = 0) -> BacktestResult:
    base = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=day0)
    start = Decimal(start_eq)
    end = Decimal(end_eq)
    return BacktestResult(
        symbol="BTC/USDT",
        timeframe="1h",
        starting_cash=start,
        ending_cash=end,
        bars_processed=2,
        fills=(),
        total_fees_paid=Decimal("0"),
        equity_curve=(
            EquityPoint(base, start),
            EquityPoint(base + timedelta(days=1), end),
        ),
        final_position=None,
    )


class FakeRun:
    def __init__(self, result: BacktestResult) -> None:
        self.engine = FakeEngine(result)
        self.torn_down = False

    def teardown(self) -> None:
        self.torn_down = True


class FakeEngine:
    def __init__(self, result: BacktestResult) -> None:
        self._result = result

    def run(self, bars: Any, timeframe: str) -> BacktestResult:
        return self._result


class TestIterWindows:
    def test_non_overlapping_oos_with_step_equal_oos(self) -> None:
        # 20 bars, IS=10, OOS=5, step=5 → folds at 0 and 5
        windows = iter_walk_forward_windows(20, is_bars=10, oos_bars=5, step_bars=5)
        assert windows == [(0, 10, 10, 15), (5, 15, 15, 20)]

    def test_oos_never_overlaps_is_within_a_fold(self) -> None:
        for is_s, is_e, oos_s, oos_e in iter_walk_forward_windows(
            50, is_bars=20, oos_bars=10, step_bars=10
        ):
            assert is_e == oos_s
            assert is_s < is_e <= oos_s < oos_e


class TestStitchOosEquity:
    def test_compounds_segments_without_resetting_to_starting_cash(self) -> None:
        # Fold1: 10000 → 11000 (+10%); Fold2 restarts 10000 → 12000 (+20%)
        # Stitched: 10000 → 11000, then 11000 → 13200
        curves = (
            _result("10000", "11000", day0=0),
            _result("10000", "12000", day0=10),
        )
        stitched = stitch_oos_equity(curves, starting_cash=Decimal("10000"))
        assert stitched[0].equity == Decimal("10000")
        assert stitched[1].equity == Decimal("11000")
        assert stitched[-1].equity == Decimal("13200")


class TestWalkForwardRunner:
    def test_produces_at_least_two_folds_without_is_in_oos(self, make_bar) -> None:
        bars = [
            make_bar(
                timestamp=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=i),
                close="100",
            )
            for i in range(30)
        ]
        call_log: list[tuple[str, tuple[Any, ...], int]] = []

        def factory(params: Any) -> FakeRun:
            # Distinct fake results; params ignored for scoring simplicity
            return FakeRun(_result("10000", "10100"))

        # Wrap to log bar ranges: monkey via custom factory that WalkForwardRunner
        # calls for both IS search and OOS — we intercept by subclassing runner logic
        # through a factory that records len(bars) via engine.run... Use a custom
        # optimizer path by making FakeEngine capture bars.

        class CapturingEngine:
            def __init__(self, label: str) -> None:
                self.label = label

            def run(self, window_bars: Any, timeframe: str) -> BacktestResult:
                call_log.append(
                    (
                        self.label,
                        (window_bars[0].timestamp, window_bars[-1].timestamp),
                        len(window_bars),
                    )
                )
                return _result("10000", "10100")

        class CapturingRun:
            def __init__(self, label: str) -> None:
                self.engine = CapturingEngine(label)
                self._label = label

            def teardown(self) -> None:
                pass

        # Each factory call is either IS trial or OOS — we can't distinguish easily.
        # Instead assert fold index ranges from the result object.
        runner = WalkForwardRunner(
            lambda params: CapturingRun("run"),
            is_bars=10,
            oos_bars=5,
            step_bars=5,
            param_grid={"fast_period": [5, 10]},
            objective="total_return_pct",
            starting_cash=Decimal("10000"),
        )
        result = runner.run(bars, "1h")

        assert result.fold_count >= 2
        for fold in result.folds:
            # OOS index range must not overlap IS within the fold
            assert fold.oos_start_index == fold.is_end_index
            assert fold.is_start_index < fold.is_end_index
            assert fold.oos_start_index < fold.oos_end_index
            # OOS bars are exactly the slice — no IS indices inside
            oos_indices = set(range(fold.oos_start_index, fold.oos_end_index))
            is_indices = set(range(fold.is_start_index, fold.is_end_index))
            assert oos_indices.isdisjoint(is_indices)

    def test_insufficient_bars_raises(self, make_bar) -> None:
        bars = [make_bar(timestamp=datetime(2024, 1, 1, tzinfo=UTC))]
        runner = WalkForwardRunner(
            lambda params: FakeRun(_result("10000", "10000")),
            is_bars=10,
            oos_bars=5,
            step_bars=5,
            param_grid={"a": [1]},
            objective="total_return_pct",
        )
        with pytest.raises(MarketDataError, match="not enough bars"):
            runner.run(bars, "1h")
