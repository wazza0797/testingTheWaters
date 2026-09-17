from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from trading_platform.domain.models.bar import Bar
from trading_platform.domain.models.signal import Signal, SignalType
from trading_platform.domain.ports.strategy import StrategyContext
from trading_platform.indicators import build_default_registry
from trading_platform.strategies.bar_window import BarWindow
from trading_platform.strategies.rules import (
    LongShortPlaybook,
    PlaybookEdgeState,
    evaluate_playbook_signals,
    parse_playbook,
)

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
    no code changes; only the entry/exit params differ between recipes.

    **Two config shapes** (parsed by `strategies/rules/playbook.py`,
    mutually exclusive — mixing them raises `ValueError` at construction):

    - **Legacy long-only**: `entry` + `exit` — unchanged since Milestone
      3.5; every existing recipe keeps working with no config changes.
    - **Long/short** (for CFD/FX venues where `InstrumentRules.allows_short`
      — see `risk/engine.py`): any of `long_entry`/`long_exit`/
      `short_entry`/`short_exit`. Each configured side's entry and exit
      must be given together; at least one side must be configured.

    Optional ATR-multiple risk levels sit beside the trees (legacy
    `stop_atr`/`take_profit_atr`, or `long_stop_atr`/
    `long_take_profit_atr`/`short_stop_atr`/`short_take_profit_atr`, plus
    shared `stop_atr_period`). While in a position they fire `CLOSE` when
    the bar's high/low touches `average_entry_price ± multiple * ATR`
    (snapshotted at open) — stop beats take-profit on the same bar, and
    both beat the indicator exit tree. See `evaluate_playbook_signals`.

    **Signal semantics** (mirrors `SmaCrossoverStrategy`'s crossover
    discipline — fire once per transition, not every bar the condition
    holds — see `evaluate_playbook_signals` for the exact rules, including
    the same-bar long+short-edge tie-break):

    - `BUY` fires when `long_entry` (or legacy `entry`) becomes true
      **and** the symbol is currently flat (`ctx.position_for`).
    - `SELL` fires when `short_entry` becomes true and flat.
    - `CLOSE` fires when a configured stop/take-profit is touched, or when
      the exit tree matching the currently open side (`long_exit` while
      long, `short_exit` while short; legacy `exit` always means
      `long_exit`) becomes true.

    Known, deliberate limitations shared with `SmaCrossoverStrategy`:

    - If an entry stays true for many bars without ever actually opening a
      position (e.g. the risk engine rejected the signal for insufficient
      cash), this strategy will not re-fire until that entry next
      transitions false-then-true again — it tracks *its own* edge, not
      "flat and entry still holds".
    - No same-bar flip: a long can only ever be closed via `CLOSE`, never
      flipped directly into a short in one bar (and vice versa) — opening
      the opposite side only happens once flat again, on a later bar.
    """

    def __init__(
        self,
        entry: Mapping[str, Any] | None = None,
        exit: Mapping[str, Any] | None = None,
        long_entry: Mapping[str, Any] | None = None,
        long_exit: Mapping[str, Any] | None = None,
        short_entry: Mapping[str, Any] | None = None,
        short_exit: Mapping[str, Any] | None = None,
        stop_atr: float | None = None,
        take_profit_atr: float | None = None,
        long_stop_atr: float | None = None,
        long_take_profit_atr: float | None = None,
        short_stop_atr: float | None = None,
        short_take_profit_atr: float | None = None,
        stop_atr_period: int | None = None,
        lookback: int = 250,
    ) -> None:
        available = build_default_registry().available()
        raw: dict[str, Any] = {}
        if entry is not None:
            raw["entry"] = entry
        if exit is not None:
            raw["exit"] = exit
        if long_entry is not None:
            raw["long_entry"] = long_entry
        if long_exit is not None:
            raw["long_exit"] = long_exit
        if short_entry is not None:
            raw["short_entry"] = short_entry
        if short_exit is not None:
            raw["short_exit"] = short_exit
        if stop_atr is not None:
            raw["stop_atr"] = stop_atr
        if take_profit_atr is not None:
            raw["take_profit_atr"] = take_profit_atr
        if long_stop_atr is not None:
            raw["long_stop_atr"] = long_stop_atr
        if long_take_profit_atr is not None:
            raw["long_take_profit_atr"] = long_take_profit_atr
        if short_stop_atr is not None:
            raw["short_stop_atr"] = short_stop_atr
        if short_take_profit_atr is not None:
            raw["short_take_profit_atr"] = short_take_profit_atr
        if stop_atr_period is not None:
            raw["stop_atr_period"] = stop_atr_period
        self._playbook: LongShortPlaybook = parse_playbook(
            raw, available, label="RuleStrategy config"
        )

        self._window = BarWindow(lookback)
        self._state = PlaybookEdgeState()

    def on_start(self, ctx: StrategyContext) -> None:
        self._window.clear()
        self._state.reset()

    def on_bar(self, bar: Bar, ctx: StrategyContext) -> list[Signal]:
        bars = self._window.append(bar)

        position = ctx.position_for(bar.symbol)
        flat = position is None or position.is_flat
        is_short = position is not None and position.quantity < 0
        entry_price = None if flat or position is None else position.average_entry_price

        fires = evaluate_playbook_signals(
            self._playbook,
            self._state,
            bars,
            ctx,
            flat=flat,
            is_short=is_short,
            entry_price=entry_price,
        )
        return [self._signal(bar, signal_type, trace) for signal_type, trace in fires]

    @staticmethod
    def _signal(bar: Bar, signal_type: SignalType, trace: Mapping[str, Any]) -> Signal:
        return Signal(
            symbol=bar.symbol,
            signal_type=signal_type,
            strategy_name=_PLACEHOLDER_STRATEGY_NAME,
            timestamp=bar.timestamp,
            metadata=dict(trace),
        )

    def on_stop(self, ctx: StrategyContext) -> None:
        pass
