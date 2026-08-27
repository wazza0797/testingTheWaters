from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from trading_platform.backtesting.result import BacktestResult
from trading_platform.container import BacktestRun
from trading_platform.domain.errors import ConfigurationError, MarketDataError
from trading_platform.domain.models.bar import Bar
from trading_platform.utils.time import to_utc


@dataclass(frozen=True, slots=True)
class HoldOutResult:
    """Paired in-sample and out-of-sample backtest results from one hold-out run.

    OOS is the only result that counts for strategy validation — IS exists so
    you can tune params without contaminating the held-out window.
    """

    is_result: BacktestResult
    oos_result: BacktestResult


def slice_bars(
    bars: Sequence[Bar],
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[Bar]:
    """Filter chronologically-sorted bars to `[start, end)` — same semantics as
    `IMarketDataRepository.load_bars` (`start` inclusive, `end` exclusive).
    """
    start_utc = to_utc(start) if start is not None else None
    end_utc = to_utc(end) if end is not None else None
    result: list[Bar] = []
    for bar in bars:
        if start_utc is not None and bar.timestamp < start_utc:
            continue
        if end_utc is not None and bar.timestamp >= end_utc:
            continue
        result.append(bar)
    return result


class HoldOutValidator:
    """Runs the same strategy twice: once on an in-sample window and once on a
    held-out out-of-sample window.

    Each window gets a fresh `BacktestRun` from `engine_factory` (and
    `teardown()` after) so event-bus subscriptions and strategy state never
    leak from IS into OOS. An optional gap between `train_end` and
    `test_start` is an embargo period (useful when indicators need warmup
    bars before OOS trading should count).
    """

    def __init__(self, engine_factory: Callable[[], BacktestRun]) -> None:
        self._engine_factory = engine_factory

    def run(
        self,
        bars: Sequence[Bar],
        timeframe: str,
        *,
        train_end: datetime,
        test_start: datetime,
        test_end: datetime | None = None,
    ) -> HoldOutResult:
        train_end_utc = to_utc(train_end)
        test_start_utc = to_utc(test_start)
        test_end_utc = to_utc(test_end) if test_end is not None else None

        if test_start_utc < train_end_utc:
            raise ConfigurationError(
                f"validation.test_start ({test_start_utc.isoformat()}) must be "
                f">= validation.train_end ({train_end_utc.isoformat()}) — "
                "overlapping IS/OOS windows defeat the hold-out"
            )

        is_bars = slice_bars(bars, end=train_end_utc)
        oos_bars = slice_bars(bars, start=test_start_utc, end=test_end_utc)

        if not is_bars:
            raise MarketDataError(
                "in-sample window is empty — check validation.train_end against "
                "the loaded bar range"
            )
        if not oos_bars:
            raise MarketDataError(
                "out-of-sample window is empty — check validation.test_start "
                "(and test_end) against the loaded bar range"
            )

        is_result = self._run_window(is_bars, timeframe)
        oos_result = self._run_window(oos_bars, timeframe)
        return HoldOutResult(is_result=is_result, oos_result=oos_result)

    def _run_window(self, bars: Sequence[Bar], timeframe: str) -> BacktestResult:
        run = self._engine_factory()
        try:
            return run.engine.run(bars, timeframe)
        finally:
            run.teardown()
