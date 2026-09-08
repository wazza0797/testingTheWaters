from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from trading_platform.domain.models.bar import Bar
from trading_platform.domain.models.signal import Signal, SignalType
from trading_platform.domain.ports.strategy import StrategyContext
from trading_platform.indicators import build_default_registry
from trading_platform.strategies.bar_window import BarWindow
from trading_platform.strategies.rules import Condition, evaluate, parse_condition
from trading_platform.strategies.rules.evaluator import TriBool
from trading_platform.strategies.rules.parser import validate_condition_indicators

# Same rationale as SmaCrossoverStrategy/RuleStrategy's placeholder:
# StrategyHandler always overwrites Signal.strategy_name with a per-instance
# identity before publishing (see strategies/loader.py::describe_strategy).
_PLACEHOLDER_STRATEGY_NAME = "regime_router"

#: Only value currently supported for the `default` param — see the
#: composable-strategies milestone doc for why a richer "default playbook"
#: is deferred rather than half-implemented.
_SUPPORTED_DEFAULT = "flat"


class _RegimeSlot:
    """One parsed `regimes[]` entry: a `when` predicate plus its own nested
    entry/exit playbook (reusing the exact same condition AST/evaluator as
    `RuleStrategy` — regimes are strategy *selection*, not a different
    rule language). Mutable: tracks this regime's own entry/exit edge state
    independently of every other regime's, reset via `reset_edges()`
    whenever this regime is (re)activated so a stale edge from a previous
    activation can never suppress a legitimate fresh signal.
    """

    __slots__ = ("name", "when", "entry", "exit", "prev_entry", "prev_exit")

    def __init__(self, name: str, when: Condition, entry: Condition, exit: Condition) -> None:
        self.name = name
        self.when = when
        self.entry = entry
        self.exit = exit
        self.prev_entry: TriBool = None
        self.prev_exit: TriBool = None

    def reset_edges(self) -> None:
        self.prev_entry = None
        self.prev_exit = None


class RegimeRouterStrategy:
    """Regime detection driving strategy *selection*: each `regimes[]` entry
    is a `when` condition (the same AND/OR/NOT tree as `RuleStrategy`, e.g.
    `EMA50 > EMA200 AND ADX > 25 AND vol_percentile > 50`) paired with its
    own nested entry/exit playbook. On every bar, the **first** regime
    whose `when` evaluates `True` becomes active; only that regime's
    playbook may open new positions while it is active — see the
    composable-strategies milestone doc for the full design write-up and
    worked YAML example.

    **Hysteresis** (`min_regime_bars`): once a regime activates, it stays
    active for at least `min_regime_bars` bars before a different `when`
    match is allowed to switch it out — prevents whipsawing between
    regimes on noisy, borderline bars. Does not delay the very first
    activation from the initial "no regime active" state.

    **Regime switches while holding a position**: this strategy does not
    keep routing bars through a deactivated regime's own exit tree once a
    switch happens (it may not even be re-evaluated for a long time). If a
    position is open at the moment `when` selects a *different* regime (or
    no regime — the `default: "flat"` state), this strategy emits an
    unconditional `CLOSE` on that same bar as a safety-net flatten, rather
    than trusting a playbook it just walked away from to still be a
    sensible exit signal for what may now be a different market regime.
    `PassThroughRiskEngine`'s pending-order check means a same-bar flatten
    `CLOSE` plus a freshly-activated regime's `BUY` never double-fire: the
    `BUY` is naturally deferred to the next bar once the `CLOSE` actually
    fills.

    `default` currently only supports `"flat"` (no regime matched -> no new
    entries, safety-net flatten as above) — present as an explicit param
    for forward-compatible YAML schema, not because another value is
    silently accepted.
    """

    def __init__(
        self,
        regimes: Sequence[Mapping[str, Any]],
        lookback: int = 250,
        min_regime_bars: int = 1,
        default: str = "flat",
    ) -> None:
        if not regimes:
            raise ValueError("regimes must be a non-empty list")
        if min_regime_bars < 1:
            raise ValueError(f"min_regime_bars must be >= 1, got {min_regime_bars}")
        if default != _SUPPORTED_DEFAULT:
            raise ValueError(
                f"default must be {_SUPPORTED_DEFAULT!r} (the only currently supported "
                f"value), got {default!r}"
            )

        available = build_default_registry().available()
        self._regimes = [self._parse_regime(raw, available) for raw in regimes]
        names = [regime.name for regime in self._regimes]
        if len(set(names)) != len(names):
            raise ValueError(f"regime names must be unique, got {names}")

        self._window = BarWindow(lookback)
        self._min_regime_bars = min_regime_bars
        self._active_index: int | None = None
        self._bars_since_switch = 0

    @staticmethod
    def _parse_regime(raw: Mapping[str, Any], available: list[str]) -> _RegimeSlot:
        missing = {"name", "when", "playbook"} - raw.keys()
        if missing:
            raise ValueError(f"regime missing required key(s) {sorted(missing)}: {dict(raw)!r}")
        name = raw["name"]
        if not isinstance(name, str) or not name:
            raise ValueError(f"regime 'name' must be a non-empty string, got {name!r}")
        playbook = raw["playbook"]
        if not isinstance(playbook, Mapping) or "entry" not in playbook or "exit" not in playbook:
            raise ValueError(
                f"regime {name!r} 'playbook' must be a mapping with 'entry' and 'exit', "
                f"got {playbook!r}"
            )

        when = parse_condition(raw["when"])
        entry = parse_condition(playbook["entry"])
        exit_condition = parse_condition(playbook["exit"])
        validate_condition_indicators(when, available)
        validate_condition_indicators(entry, available)
        validate_condition_indicators(exit_condition, available)

        return _RegimeSlot(name=name, when=when, entry=entry, exit=exit_condition)

    def on_start(self, ctx: StrategyContext) -> None:
        self._window.clear()
        self._active_index = None
        self._bars_since_switch = 0
        for regime in self._regimes:
            regime.reset_edges()

    def on_bar(self, bar: Bar, ctx: StrategyContext) -> list[Signal]:
        bars = self._window.append(bar)
        matched = self._select_matching_regime(bars, ctx)

        locked = self._active_index is not None and self._bars_since_switch < self._min_regime_bars
        target = self._active_index if locked else matched

        signals: list[Signal] = []
        if target != self._active_index:
            signals.extend(self._handle_switch(bar, ctx, target))
        else:
            self._bars_since_switch += 1

        if self._active_index is not None:
            signals.extend(
                self._evaluate_playbook(self._regimes[self._active_index], bar, bars, ctx)
            )

        return signals

    def _select_matching_regime(self, bars: list[Bar], ctx: StrategyContext) -> int | None:
        for i, regime in enumerate(self._regimes):
            if evaluate(regime.when, bars, ctx) is True:
                return i
        return None

    def _handle_switch(self, bar: Bar, ctx: StrategyContext, target: int | None) -> list[Signal]:
        signals: list[Signal] = []
        position = ctx.position_for(bar.symbol)
        if position is not None and not position.is_flat:
            new_regime_name = "flat" if target is None else self._regimes[target].name
            signals.append(
                Signal(
                    symbol=bar.symbol,
                    signal_type=SignalType.CLOSE,
                    strategy_name=_PLACEHOLDER_STRATEGY_NAME,
                    timestamp=bar.timestamp,
                    metadata={"reason": "regime_switch", "new_regime": new_regime_name},
                )
            )
        if target is not None:
            self._regimes[target].reset_edges()
        self._active_index = target
        self._bars_since_switch = 1
        return signals

    def _evaluate_playbook(
        self, regime: _RegimeSlot, bar: Bar, bars: list[Bar], ctx: StrategyContext
    ) -> list[Signal]:
        entry_trace: dict[str, TriBool] = {}
        exit_trace: dict[str, TriBool] = {}
        entry_now = evaluate(regime.entry, bars, ctx, entry_trace)
        exit_now = evaluate(regime.exit, bars, ctx, exit_trace)

        position = ctx.position_for(bar.symbol)
        flat = position is None or position.is_flat

        signals: list[Signal] = []
        if flat and entry_now is True and regime.prev_entry is not True:
            signals.append(self._signal(bar, SignalType.BUY, regime.name, entry_trace))
        if not flat and exit_now is True and regime.prev_exit is not True:
            signals.append(self._signal(bar, SignalType.CLOSE, regime.name, exit_trace))

        regime.prev_entry = entry_now
        regime.prev_exit = exit_now
        return signals

    @staticmethod
    def _signal(
        bar: Bar, signal_type: SignalType, regime_name: str, trace: Mapping[str, TriBool]
    ) -> Signal:
        return Signal(
            symbol=bar.symbol,
            signal_type=signal_type,
            strategy_name=_PLACEHOLDER_STRATEGY_NAME,
            timestamp=bar.timestamp,
            metadata={"regime": regime_name, **trace},
        )

    def on_stop(self, ctx: StrategyContext) -> None:
        pass
