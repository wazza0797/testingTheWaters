from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from trading_platform.domain.models.bar import Bar
from trading_platform.domain.models.signal import Signal, SignalType
from trading_platform.domain.ports.strategy import StrategyContext
from trading_platform.indicators import build_default_registry
from trading_platform.strategies.bar_window import BarWindow
from trading_platform.strategies.rules import Condition, evaluate, parse_condition
from trading_platform.strategies.rules.evaluator import TriBool
from trading_platform.strategies.rules.parser import validate_condition_indicators

# Same rationale as `SmaCrossoverStrategy`'s placeholder: `StrategyHandler`
# always overwrites `Signal.strategy_name` with a per-instance identity
# before publishing (see `strategies/loader.py::describe_strategy`).
_PLACEHOLDER_STRATEGY_NAME = "rule_strategy"


class RuleStrategy:
    """A config-driven strategy: entry/exit are each an arbitrary
    AND/OR/NOT tree over any mix of indicators (see
    `strategies/rules/` and the composable-strategies milestone doc) —
    not a fixed "one indicator per category" gate. A recipe can mix two
    volume checks and one volatility check, or nest `(X OR Y) AND Z`, with
    no code changes; only `params.entry`/`params.exit` differ between
    recipes.

    **Signal semantics** (mirrors `SmaCrossoverStrategy`'s crossover
    discipline — fire once per transition, not every bar the condition
    holds):

    - `BUY` fires when `entry` becomes true (`False`/`None` -> `True`)
      **and** the symbol is currently flat (`ctx.position_for`).
    - `CLOSE` fires when `exit` becomes true **and** the symbol currently
      holds a position.

    Both trees use the Kleene 3-valued evaluator (`strategies/rules/evaluator.py`):
    `None` ("not enough history yet") is never treated as a transition, so a
    strategy never fires during indicator warmup.

    Known, deliberate limitation shared with `SmaCrossoverStrategy`: if
    `entry` stays true for many bars without ever actually opening a
    position (e.g. the risk engine rejected the `BUY` for insufficient
    cash), this strategy will not re-fire `BUY` until `entry` next
    transitions false-then-true again — it tracks *its own* edge, not
    "flat and entry still holds".
    """

    def __init__(
        self,
        entry: Mapping[str, Any],
        exit: Mapping[str, Any],
        lookback: int = 250,
    ) -> None:
        self._entry: Condition = parse_condition(entry)
        self._exit: Condition = parse_condition(exit)
        available = build_default_registry().available()
        validate_condition_indicators(self._entry, available)
        validate_condition_indicators(self._exit, available)

        self._window = BarWindow(lookback)
        self._prev_entry: TriBool = None
        self._prev_exit: TriBool = None

    def on_start(self, ctx: StrategyContext) -> None:
        self._window.clear()
        self._prev_entry = None
        self._prev_exit = None

    def on_bar(self, bar: Bar, ctx: StrategyContext) -> list[Signal]:
        bars = self._window.append(bar)

        entry_trace: dict[str, TriBool] = {}
        exit_trace: dict[str, TriBool] = {}
        entry_now = evaluate(self._entry, bars, ctx, entry_trace)
        exit_now = evaluate(self._exit, bars, ctx, exit_trace)

        position = ctx.position_for(bar.symbol)
        flat = position is None or position.is_flat

        signals: list[Signal] = []
        if flat and entry_now is True and self._prev_entry is not True:
            signals.append(self._signal(bar, SignalType.BUY, entry_trace))
        if not flat and exit_now is True and self._prev_exit is not True:
            signals.append(self._signal(bar, SignalType.CLOSE, exit_trace))

        self._prev_entry = entry_now
        self._prev_exit = exit_now
        return signals

    @staticmethod
    def _signal(bar: Bar, signal_type: SignalType, trace: Mapping[str, TriBool]) -> Signal:
        return Signal(
            symbol=bar.symbol,
            signal_type=signal_type,
            strategy_name=_PLACEHOLDER_STRATEGY_NAME,
            timestamp=bar.timestamp,
            metadata=dict(trace),
        )

    def on_stop(self, ctx: StrategyContext) -> None:
        pass
