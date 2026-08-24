from __future__ import annotations

import math
from collections import deque

from trading_platform.domain.models.bar import Bar
from trading_platform.domain.models.signal import Signal, SignalType
from trading_platform.domain.ports.strategy import StrategyContext

_STRATEGY_NAME = "sma_crossover"


class SmaCrossoverStrategy:
    """Reference strategy: a classic fast/slow SMA crossover.

    Emits `BUY` on a golden cross (fast SMA crosses from at-or-below to
    strictly above the slow SMA) and `SELL` on a death cross (the mirror
    case) — **only on the bar where the cross happens**, not on every bar
    the relationship holds, so a sustained trend produces exactly one signal
    per direction change rather than a flood of duplicates.

    Demonstrates the full `IStrategy` contract: zero imports from
    `exchanges/`, `execution/`, or `ccxt`; fully testable with synthetic
    `Bar` sequences via `ctx.indicator(...)` (no event bus or exchange
    needed). Maintains its own rolling bar buffer internally since
    `on_bar` only ever receives the single newly-closed bar.
    """

    def __init__(self, fast_period: int = 10, slow_period: int = 30) -> None:
        if fast_period < 1 or slow_period < 1:
            raise ValueError(
                f"fast_period and slow_period must be >= 1, got {fast_period}/{slow_period}"
            )
        if fast_period >= slow_period:
            raise ValueError(
                f"fast_period ({fast_period}) must be strictly less than "
                f"slow_period ({slow_period})"
            )
        self._fast_period = fast_period
        self._slow_period = slow_period
        self._bars: deque[Bar] = deque(maxlen=slow_period + 1)
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None

    @property
    def fast_period(self) -> int:
        return self._fast_period

    @property
    def slow_period(self) -> int:
        return self._slow_period

    def on_start(self, ctx: StrategyContext) -> None:
        self._bars.clear()
        self._prev_fast = None
        self._prev_slow = None

    def on_bar(self, bar: Bar, ctx: StrategyContext) -> list[Signal]:
        self._bars.append(bar)
        bars = list(self._bars)
        fast = ctx.indicator("sma", bars, period=self._fast_period)
        slow = ctx.indicator("sma", bars, period=self._slow_period)

        signals = self._signals_for_cross(bar, fast, slow)

        self._prev_fast = fast
        self._prev_slow = slow
        return signals

    def _signals_for_cross(self, bar: Bar, fast: float, slow: float) -> list[Signal]:
        prev_fast, prev_slow = self._prev_fast, self._prev_slow
        if (
            prev_fast is None
            or prev_slow is None
            or math.isnan(prev_fast)
            or math.isnan(prev_slow)
            or math.isnan(fast)
            or math.isnan(slow)
        ):
            return []

        metadata = {"fast_sma": fast, "slow_sma": slow}
        if prev_fast <= prev_slow and fast > slow:
            return [self._signal(bar, SignalType.BUY, metadata)]
        if prev_fast >= prev_slow and fast < slow:
            return [self._signal(bar, SignalType.SELL, metadata)]
        return []

    @staticmethod
    def _signal(bar: Bar, signal_type: SignalType, metadata: dict[str, float]) -> Signal:
        return Signal(
            symbol=bar.symbol,
            signal_type=signal_type,
            strategy_name=_STRATEGY_NAME,
            timestamp=bar.timestamp,
            metadata=metadata,
        )

    def on_stop(self, ctx: StrategyContext) -> None:
        pass
