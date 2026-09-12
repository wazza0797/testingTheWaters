from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from trading_platform.domain.models.bar import Bar
from trading_platform.domain.models.signal import Signal, SignalType
from trading_platform.domain.ports.strategy import StrategyContext
from trading_platform.indicators import build_default_registry
from trading_platform.strategies.bar_window import BarWindow
from trading_platform.strategies.rules import (
    Condition,
    LongShortPlaybook,
    PlaybookEdgeState,
    evaluate,
    evaluate_playbook_signals,
    parse_condition,
    parse_playbook,
    validate_condition_indicators,
)

# Same rationale as SmaCrossoverStrategy/RuleStrategy's placeholder:
# StrategyHandler always overwrites Signal.strategy_name with a per-instance
# identity before publishing (see strategies/loader.py::describe_strategy).
_PLACEHOLDER_STRATEGY_NAME = "regime_router"

#: Only value currently supported for the `default` param — see the
#: composable-strategies milestone doc for why a richer "default playbook"
#: is deferred rather than half-implemented.
_SUPPORTED_DEFAULT = "flat"


def _validate_entry_hours(start: int | None, end: int | None) -> tuple[int | None, int | None]:
    """Validate optional `[start, end)` UTC hour window for new entries.

    Both None → no session filter. Both set → hours in `0..23`, end exclusive.
    `start < end` is a same-day window; `start > end` wraps across midnight.
    Exactly one set is rejected.
    """
    if start is None and end is None:
        return None, None
    if start is None or end is None:
        raise ValueError(
            "entry_hour_start_utc and entry_hour_end_utc must both be set or both omitted"
        )
    if not 0 <= start <= 23 or not 0 <= end <= 23:
        raise ValueError(f"entry hours must be in 0..23, got start={start}, end={end}")
    if start == end:
        raise ValueError("entry_hour_start_utc and entry_hour_end_utc must differ")
    return start, end


class _RegimeSlot:
    """One parsed `regimes[]` entry: a `when` predicate plus its own nested
    long/short playbook (`strategies/rules/playbook.py` — the same
    long/short parsing and edge-tracked signal emission `RuleStrategy`
    uses; regimes are strategy *selection*, not a different rule
    language). Mutable: tracks this regime's own playbook edge state
    independently of every other regime's, reset via `reset_edges()`
    whenever this regime is (re)activated so a stale edge from a previous
    activation can never suppress a legitimate fresh signal.
    """

    __slots__ = ("name", "when", "playbook", "edge_state")

    def __init__(self, name: str, when: Condition, playbook: LongShortPlaybook) -> None:
        self.name = name
        self.when = when
        self.playbook = playbook
        self.edge_state = PlaybookEdgeState()

    def reset_edges(self) -> None:
        self.edge_state.reset()


class RegimeRouterStrategy:
    """Regime detection driving strategy *selection*: each `regimes[]` entry
    is a `when` condition (the same AND/OR/NOT tree as `RuleStrategy`, e.g.
    `EMA50 > EMA200 AND ADX > 25 AND vol_percentile > 50`) paired with its
    own nested playbook. On every bar, the **first** regime whose `when`
    evaluates `True` becomes active; only that regime's playbook may open
    new positions while it is active — see the composable-strategies
    milestone doc for the full design write-up and worked YAML example.

    **Playbook shape** (per regime, parsed by `strategies/rules/playbook.py`
    — identical rules to `RuleStrategy`): either legacy long-only
    `{"entry": ..., "exit": ...}`, or long/short via any of
    `long_entry`/`long_exit`/`short_entry`/`short_exit` (each side's entry
    and exit given together; at least one side configured; mixing the two
    shapes is rejected). Optional ATR-multiple `*_stop_atr` /
    `*_take_profit_atr` (and shared `stop_atr_period`) may sit beside the
    trees — same semantics as `RuleStrategy`. A `BUY`/`SELL` opens the
    matching side while flat; stop/TP or the exit tree for whichever side
    is currently open fires `CLOSE`.

    **Hysteresis** (`min_regime_bars`): once a regime activates, it stays
    active for at least `min_regime_bars` bars before a different `when`
    match is allowed to switch it out — prevents whipsawing between
    regimes on noisy, borderline bars. Does not delay the very first
    activation from the initial "no regime active" state.

    **Regime switches while holding a position**: this strategy does not
    keep routing bars through a deactivated regime's own exit tree once a
    switch happens (it may not even be re-evaluated for a long time). If a
    position (long or short) is open at the moment `when` selects a
    *different* regime (or no regime — the `default: "flat"` state), this
    strategy emits an unconditional `CLOSE` on that same bar as a
    safety-net flatten, rather than trusting a playbook it just walked
    away from to still be a sensible exit signal for what may now be a
    different market regime. `PassThroughRiskEngine`'s pending-order check
    means a same-bar flatten `CLOSE` plus a freshly-activated regime's
    `BUY`/`SELL` never double-fire: the new entry is naturally deferred to
    the next bar once the `CLOSE` actually fills.

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
        entry_hour_start_utc: int | None = None,
        entry_hour_end_utc: int | None = None,
        flatten_hour_utc: int | None = None,
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
        self._entry_hour_start_utc, self._entry_hour_end_utc = _validate_entry_hours(
            entry_hour_start_utc, entry_hour_end_utc
        )
        if flatten_hour_utc is not None and not 0 <= flatten_hour_utc <= 23:
            raise ValueError(f"flatten_hour_utc must be in 0..23, got {flatten_hour_utc}")
        self._flatten_hour_utc = flatten_hour_utc

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
        playbook_raw = raw["playbook"]
        if not isinstance(playbook_raw, Mapping):
            raise ValueError(f"regime {name!r} 'playbook' must be a mapping, got {playbook_raw!r}")

        when = parse_condition(raw["when"])
        validate_condition_indicators(when, available)
        playbook = parse_playbook(playbook_raw, available, label=f"regime {name!r} 'playbook'")

        return _RegimeSlot(name=name, when=when, playbook=playbook)

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

        if self._should_session_flatten(bar, ctx):
            # Force flat near cash-session close; skip playbook entries this bar.
            signals.append(
                Signal(
                    symbol=bar.symbol,
                    signal_type=SignalType.CLOSE,
                    strategy_name=_PLACEHOLDER_STRATEGY_NAME,
                    timestamp=bar.timestamp,
                    metadata={"reason": "session_flatten", "hour_utc": bar.timestamp.hour},
                )
            )
            return signals

        if self._active_index is not None:
            playbook_signals = self._evaluate_playbook(
                self._regimes[self._active_index], bar, bars, ctx
            )
            if not self._in_entry_window(bar):
                playbook_signals = [
                    s for s in playbook_signals if s.signal_type == SignalType.CLOSE
                ]
            signals.extend(playbook_signals)

        return signals

    def _in_entry_window(self, bar: Bar) -> bool:
        start = self._entry_hour_start_utc
        end = self._entry_hour_end_utc
        if start is None or end is None:
            return True
        hour = bar.timestamp.hour
        if start < end:
            return start <= hour < end
        # Wrap across midnight (e.g. 22..6).
        return hour >= start or hour < end

    def _should_session_flatten(self, bar: Bar, ctx: StrategyContext) -> bool:
        if self._flatten_hour_utc is None:
            return False
        position = ctx.position_for(bar.symbol)
        if position is None or position.is_flat:
            return False
        return bar.timestamp.hour >= self._flatten_hour_utc

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
        position = ctx.position_for(bar.symbol)
        flat = position is None or position.is_flat
        is_short = position is not None and position.quantity < 0
        entry_price = None if flat or position is None else position.average_entry_price

        fires = evaluate_playbook_signals(
            regime.playbook,
            regime.edge_state,
            bars,
            ctx,
            flat=flat,
            is_short=is_short,
            entry_price=entry_price,
        )
        return [self._signal(bar, signal_type, regime.name, trace) for signal_type, trace in fires]

    @staticmethod
    def _signal(
        bar: Bar, signal_type: SignalType, regime_name: str, trace: Mapping[str, Any]
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
