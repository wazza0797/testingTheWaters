"""Long/short entry+exit playbooks shared by `RuleStrategy` and
`RegimeRouterStrategy` — one place for the parsing rules and edge-tracked
signal emission both strategies need, so CFD/FX two-sided recipes (and the
legacy long-only shape) are defined and evaluated identically everywhere.

Optional ATR-multiple stop-loss / take-profit levels sit *beside* the
condition trees (not inside them): the condition DSL has no way to reference
`Position.average_entry_price` or bar high/low, so stop/TP are sibling
numeric keys evaluated against the live position's entry and the current
bar's OHLC. See `evaluate_playbook_signals`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from math import isnan
from typing import Any

from trading_platform.domain.models.bar import Bar
from trading_platform.domain.models.signal import SignalType
from trading_platform.domain.ports.strategy import StrategyContext
from trading_platform.strategies.rules.ast import Condition
from trading_platform.strategies.rules.evaluator import TriBool, evaluate
from trading_platform.strategies.rules.parser import parse_condition, validate_condition_indicators

_LEGACY_KEYS = frozenset({"entry", "exit", "stop_atr", "take_profit_atr"})
_LONG_SHORT_KEYS = frozenset(
    {
        "long_entry",
        "long_exit",
        "short_entry",
        "short_exit",
        "long_stop_atr",
        "long_take_profit_atr",
        "short_stop_atr",
        "short_take_profit_atr",
    }
)
_SHARED_KEYS = frozenset({"stop_atr_period"})
_DEFAULT_STOP_ATR_PERIOD = 14


@dataclass(frozen=True, slots=True)
class LongShortPlaybook:
    """Parsed condition trees plus optional ATR-multiple risk levels.

    `long_entry`/`long_exit` gate opening/closing a long position;
    `short_entry`/`short_exit` gate opening/closing a short. Either side may
    be `None` (not configured), but at least one side is always fully
    configured (both its entry and exit) — enforced by `parse_playbook`.

    `*_stop_atr` / `*_take_profit_atr` are optional multiples of ATR at the
    moment the position first appears (snapshotted into
    `PlaybookEdgeState.risk_atr`). `None` means that risk check is disabled
    for that side.
    """

    long_entry: Condition | None
    long_exit: Condition | None
    short_entry: Condition | None
    short_exit: Condition | None
    long_stop_atr: float | None = None
    long_take_profit_atr: float | None = None
    short_stop_atr: float | None = None
    short_take_profit_atr: float | None = None
    stop_atr_period: int = _DEFAULT_STOP_ATR_PERIOD

    @property
    def has_risk_levels(self) -> bool:
        return any(
            v is not None
            for v in (
                self.long_stop_atr,
                self.long_take_profit_atr,
                self.short_stop_atr,
                self.short_take_profit_atr,
            )
        )


def parse_playbook(
    raw: Mapping[str, Any], available: Sequence[str], *, label: str = "playbook"
) -> LongShortPlaybook:
    """Parse a playbook mapping supporting two mutually-exclusive shapes:

    - **Legacy long-only**: `{"entry": ..., "exit": ...}` — equivalent to
      `long_entry`/`long_exit`; no short side. Kept so every existing
      recipe (and `RuleStrategy`'s original signature) keeps working
      unchanged. Optional legacy `stop_atr` / `take_profit_atr` map onto
      the long side only.
    - **Long/short**: any of `long_entry`/`long_exit`/`short_entry`/
      `short_exit`. Each configured side's entry and exit must be given
      together; at least one side must be configured. Optional
      `long_stop_atr` / `long_take_profit_atr` / `short_stop_atr` /
      `short_take_profit_atr` are ATR multiples relative to
      `Position.average_entry_price`.

    Shared optional `stop_atr_period` (default 14) selects the ATR window
    used to size those levels.

    Mixing `entry`/`exit` with the `long_*`/`short_*` keys is rejected as
    ambiguous. Raises `ValueError` for any malformed shape (construction
    time only, never mid-run) — `label` (e.g. `"playbook"` or a strategy
    name) makes the error identify which config block is at fault.
    """
    keys = set(raw.keys())
    unknown = keys - _LEGACY_KEYS - _LONG_SHORT_KEYS - _SHARED_KEYS
    if unknown:
        raise ValueError(f"{label} has unknown key(s) {sorted(unknown)}: {dict(raw)!r}")

    uses_legacy = bool(keys & {"entry", "exit"})
    uses_long_short = bool(
        keys
        & {
            "long_entry",
            "long_exit",
            "short_entry",
            "short_exit",
            "long_stop_atr",
            "long_take_profit_atr",
            "short_stop_atr",
            "short_take_profit_atr",
        }
    )
    # Legacy stop keys alone without entry/exit still count as legacy shape
    # only when paired with entry/exit — bare stop keys with long_* trees
    # would be caught by the mix check / unknown-side rules below.
    if uses_legacy and uses_long_short:
        raise ValueError(
            f"{label} cannot mix legacy 'entry'/'exit' with "
            f"'long_entry'/'long_exit'/'short_entry'/'short_exit': {dict(raw)!r}"
        )

    if uses_legacy:
        if "entry" not in raw or "exit" not in raw:
            raise ValueError(
                f"{label} must be a mapping with 'entry' and 'exit', got {dict(raw)!r}"
            )
        long_entry_raw, long_exit_raw = raw["entry"], raw["exit"]
        short_entry_raw = short_exit_raw = None
        long_stop = _parse_optional_positive_float(raw.get("stop_atr"), "stop_atr", label)
        long_tp = _parse_optional_positive_float(
            raw.get("take_profit_atr"), "take_profit_atr", label
        )
        short_stop = short_tp = None
        if any(
            k in raw
            for k in (
                "long_stop_atr",
                "long_take_profit_atr",
                "short_stop_atr",
                "short_take_profit_atr",
            )
        ):
            raise ValueError(
                f"{label} legacy 'entry'/'exit' shape uses 'stop_atr'/'take_profit_atr', "
                f"not long_/short_ risk keys: {dict(raw)!r}"
            )
    else:
        # Not legacy — either some long_*/short_* keys were given, or `raw`
        # is empty entirely. Both fall through to the same "at least one
        # side configured" check below via `.get()` returning `None`.
        long_entry_raw = raw.get("long_entry")
        long_exit_raw = raw.get("long_exit")
        short_entry_raw = raw.get("short_entry")
        short_exit_raw = raw.get("short_exit")
        if (long_entry_raw is None) != (long_exit_raw is None):
            raise ValueError(f"{label} 'long_entry' and 'long_exit' must be provided together")
        if (short_entry_raw is None) != (short_exit_raw is None):
            raise ValueError(f"{label} 'short_entry' and 'short_exit' must be provided together")
        if long_entry_raw is None and short_entry_raw is None:
            raise ValueError(
                f"{label} must configure at least one of long_entry/long_exit "
                f"or short_entry/short_exit, got {dict(raw)!r}"
            )
        long_stop = _parse_optional_positive_float(raw.get("long_stop_atr"), "long_stop_atr", label)
        long_tp = _parse_optional_positive_float(
            raw.get("long_take_profit_atr"), "long_take_profit_atr", label
        )
        short_stop = _parse_optional_positive_float(
            raw.get("short_stop_atr"), "short_stop_atr", label
        )
        short_tp = _parse_optional_positive_float(
            raw.get("short_take_profit_atr"), "short_take_profit_atr", label
        )
        if long_entry_raw is None and (long_stop is not None or long_tp is not None):
            raise ValueError(
                f"{label} long_stop_atr/long_take_profit_atr require long_entry/long_exit"
            )
        if short_entry_raw is None and (short_stop is not None or short_tp is not None):
            raise ValueError(
                f"{label} short_stop_atr/short_take_profit_atr require short_entry/short_exit"
            )
        if any(k in raw for k in ("stop_atr", "take_profit_atr")):
            raise ValueError(
                f"{label} long/short shape uses long_/short_ risk keys, "
                f"not legacy 'stop_atr'/'take_profit_atr': {dict(raw)!r}"
            )

    atr_period = _parse_atr_period(raw.get("stop_atr_period"), label)
    if (
        any(v is not None for v in (long_stop, long_tp, short_stop, short_tp))
        and "atr" not in available
    ):
        raise ValueError(
            f"{label} configures ATR stop/take-profit but 'atr' is not a "
            f"registered indicator (available: {sorted(available)})"
        )

    def _parse(node: Any) -> Condition | None:
        if node is None:
            return None
        condition = parse_condition(node)
        validate_condition_indicators(condition, available)
        return condition

    return LongShortPlaybook(
        long_entry=_parse(long_entry_raw),
        long_exit=_parse(long_exit_raw),
        short_entry=_parse(short_entry_raw),
        short_exit=_parse(short_exit_raw),
        long_stop_atr=long_stop,
        long_take_profit_atr=long_tp,
        short_stop_atr=short_stop,
        short_take_profit_atr=short_tp,
        stop_atr_period=atr_period,
    )


def _parse_optional_positive_float(raw: Any, key: str, label: str) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"{label} '{key}' must be a positive number, got {raw!r}")
    value = float(raw)
    if value <= 0 or isnan(value):
        raise ValueError(f"{label} '{key}' must be a positive number, got {raw!r}")
    return value


def _parse_atr_period(raw: Any, label: str) -> int:
    if raw is None:
        return _DEFAULT_STOP_ATR_PERIOD
    # bool is a subclass of int — reject explicitly so `true` never becomes period 1.
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"{label} 'stop_atr_period' must be an int >= 1, got {raw!r}")
    if raw < 1:
        raise ValueError(f"{label} 'stop_atr_period' must be an int >= 1, got {raw!r}")
    return raw


@dataclass(slots=True)
class PlaybookEdgeState:
    """Per-playbook edge memory (mirrors the single entry/exit tracking the
    original long-only `RuleStrategy` did) — tracked independently for each
    of the (up to) four condition trees so a side that's been continuously
    true since before its last fire never silently re-fires.

    Also holds the ATR snapshot used to size stop/take-profit levels for
    the currently open position (`risk_atr`). Cleared whenever the
    strategy goes flat so the next entry re-measures ATR at open time.
    """

    prev_long_entry: TriBool = field(default=None)
    prev_long_exit: TriBool = field(default=None)
    prev_short_entry: TriBool = field(default=None)
    prev_short_exit: TriBool = field(default=None)
    risk_atr: float | None = field(default=None)

    def reset(self) -> None:
        self.prev_long_entry = None
        self.prev_long_exit = None
        self.prev_short_entry = None
        self.prev_short_exit = None
        self.risk_atr = None


def evaluate_playbook_signals(
    playbook: LongShortPlaybook,
    state: PlaybookEdgeState,
    bars: Sequence[Bar],
    ctx: StrategyContext,
    *,
    flat: bool,
    is_short: bool,
    entry_price: Decimal | None = None,
) -> list[tuple[SignalType, dict[str, Any]]]:
    """Evaluate all configured condition trees (and optional ATR risk
    levels) for this bar and return the (at most one) `(SignalType, trace)`
    pair to emit, using the same once-per-transition discipline every
    strategy in this package follows for indicator exits.

    - **Flat**: a fresh `long_entry` edge fires `BUY`; a fresh `short_entry`
      edge fires `SELL`. If both edges fire on the same bar, neither is
      emitted — an unambiguous simultaneous long+short signal has no single
      correct interpretation, so this strategy takes no position rather
      than guessing (see the composable-strategies design notes). Clears
      any leftover `risk_atr` snapshot.
    - **Long** (`not flat and not is_short`): ATR stop/take-profit (if
      configured) are checked first against `bar.low`/`bar.high` relative
      to `entry_price`; a hit fires `CLOSE` every bar until flat (not
      edge-tracked — a rejected CLOSE must be able to retry). Otherwise a
      fresh `long_exit` edge fires `CLOSE`.
    - **Short** (`is_short`): same, with stop/TP directions inverted.

    Same-bar stop+TP: **stop wins** (conservative). ATR is snapshotted into
    `state.risk_atr` on the first in-position bar where ATR is defined; if
    ATR is still warming up, risk levels are skipped until it is.

    Every configured tree's edge state is updated every bar regardless of
    whether it fired, so a condition that's been continuously `True` since
    before the position last flattened doesn't spuriously re-fire the
    moment the strategy goes flat again.
    """
    long_entry_trace: dict[str, TriBool] = {}
    long_exit_trace: dict[str, TriBool] = {}
    short_entry_trace: dict[str, TriBool] = {}
    short_exit_trace: dict[str, TriBool] = {}

    long_entry_now = (
        evaluate(playbook.long_entry, bars, ctx, long_entry_trace)
        if playbook.long_entry is not None
        else None
    )
    long_exit_now = (
        evaluate(playbook.long_exit, bars, ctx, long_exit_trace)
        if playbook.long_exit is not None
        else None
    )
    short_entry_now = (
        evaluate(playbook.short_entry, bars, ctx, short_entry_trace)
        if playbook.short_entry is not None
        else None
    )
    short_exit_now = (
        evaluate(playbook.short_exit, bars, ctx, short_exit_trace)
        if playbook.short_exit is not None
        else None
    )

    results: list[tuple[SignalType, dict[str, Any]]] = []
    if flat:
        state.risk_atr = None
        long_edge = (
            playbook.long_entry is not None
            and long_entry_now is True
            and state.prev_long_entry is not True
        )
        short_edge = (
            playbook.short_entry is not None
            and short_entry_now is True
            and state.prev_short_entry is not True
        )
        if long_edge and not short_edge:
            results.append((SignalType.BUY, dict(long_entry_trace)))
        elif short_edge and not long_edge:
            results.append((SignalType.SELL, dict(short_entry_trace)))
        # else: no edge, or both fired on the same bar — emit neither.
    else:
        risk_hit = _evaluate_risk_levels(
            playbook, state, bars, ctx, is_short=is_short, entry_price=entry_price
        )
        if risk_hit is not None:
            results.append(risk_hit)
        elif is_short:
            if (
                playbook.short_exit is not None
                and short_exit_now is True
                and state.prev_short_exit is not True
            ):
                results.append((SignalType.CLOSE, dict(short_exit_trace)))
        else:
            if (
                playbook.long_exit is not None
                and long_exit_now is True
                and state.prev_long_exit is not True
            ):
                results.append((SignalType.CLOSE, dict(long_exit_trace)))

    state.prev_long_entry = long_entry_now
    state.prev_long_exit = long_exit_now
    state.prev_short_entry = short_entry_now
    state.prev_short_exit = short_exit_now
    return results


def _evaluate_risk_levels(
    playbook: LongShortPlaybook,
    state: PlaybookEdgeState,
    bars: Sequence[Bar],
    ctx: StrategyContext,
    *,
    is_short: bool,
    entry_price: Decimal | None,
) -> tuple[SignalType, dict[str, Any]] | None:
    """Return a CLOSE+trace if stop or take-profit is hit on this bar, else None."""
    if not playbook.has_risk_levels or entry_price is None or not bars:
        return None

    if state.risk_atr is None:
        # Size levels from ATR as of the *previous* bar whenever possible.
        # Using the current bar would let a stop/TP-touching spike inflate
        # ATR on the same bar it's checked against, which can push the level
        # past the touch and silently miss the exit (classic look-ahead /
        # self-referential sizing bug).
        atr_bars = bars[:-1] if len(bars) > 1 else bars
        atr = ctx.indicator("atr", atr_bars, period=playbook.stop_atr_period)
        if isnan(atr) or atr <= 0:
            atr = ctx.indicator("atr", bars, period=playbook.stop_atr_period)
        if isnan(atr) or atr <= 0:
            return None  # still warming up — skip risk until ATR is defined
        state.risk_atr = atr

    atr_d = Decimal(str(state.risk_atr))
    entry = entry_price
    bar = bars[-1]

    if is_short:
        stop_mult = playbook.short_stop_atr
        tp_mult = playbook.short_take_profit_atr
        stop_hit = stop_mult is not None and bar.high >= entry + Decimal(str(stop_mult)) * atr_d
        tp_hit = tp_mult is not None and bar.low <= entry - Decimal(str(tp_mult)) * atr_d
    else:
        stop_mult = playbook.long_stop_atr
        tp_mult = playbook.long_take_profit_atr
        stop_hit = stop_mult is not None and bar.low <= entry - Decimal(str(stop_mult)) * atr_d
        tp_hit = tp_mult is not None and bar.high >= entry + Decimal(str(tp_mult)) * atr_d

    if stop_hit:
        return (
            SignalType.CLOSE,
            {
                "exit_reason": "stop_loss",
                "stop_atr_multiple": stop_mult,
                "risk_atr": state.risk_atr,
                "entry_price": str(entry),
            },
        )
    if tp_hit:
        return (
            SignalType.CLOSE,
            {
                "exit_reason": "take_profit",
                "take_profit_atr_multiple": tp_mult,
                "risk_atr": state.risk_atr,
                "entry_price": str(entry),
            },
        )
    return None
