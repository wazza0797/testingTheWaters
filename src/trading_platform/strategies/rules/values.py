from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, TypeAlias

from trading_platform.strategies.rules.errors import ConditionError


@dataclass(frozen=True, slots=True)
class IndicatorRef:
    """A reference to a registered indicator plus whichever kwargs it
    needs (e.g. `period`, `fast_period`) — resolved each bar via
    `StrategyContext.indicator(name, bars, **params)`, so this never needs
    to know about `IndicatorRegistry` directly (the context does).
    """

    name: str
    params: Mapping[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        if not self.params:
            return self.name
        rendered = ",".join(f"{k}={v}" for k, v in sorted(self.params.items()))
        return f"{self.name}({rendered})"


@dataclass(frozen=True, slots=True)
class CloseRef:
    """A reference to the triggering bar's own close price — the literal
    string `"close"` in YAML (e.g. `cross: {left: close, right: {...}}`).
    """

    def describe(self) -> str:
        return "close"


@dataclass(frozen=True, slots=True)
class ConstantRef:
    """A fixed numeric value used as one side of a `cross`/`compare_indicators`
    leaf (rare — most fixed thresholds are `compare`'s own `value` field, but
    a raw number is also accepted anywhere a `ValueSpec` is expected).
    """

    value: float

    def describe(self) -> str:
        return str(self.value)


#: What a condition leaf compares: a named indicator (with its own kwargs),
#: the triggering bar's own close price, or a fixed numeric constant.
ValueSpec: TypeAlias = "IndicatorRef | CloseRef | ConstantRef"


def parse_value_spec(raw: object) -> ValueSpec:
    """Parse one side of a `compare_indicators`/`cross` leaf: the literal
    string `"close"`, a bare number, or `{indicator: <name>, **kwargs}`.
    """
    if isinstance(raw, str):
        if raw == "close":
            return CloseRef()
        raise ConditionError(
            f"Unrecognized value reference {raw!r}. Expected 'close' or a mapping with "
            "an 'indicator' key."
        )
    if isinstance(raw, bool):
        # bool is a subclass of int in Python; reject explicitly so a typo'd
        # `true`/`false` in YAML doesn't silently become 1.0/0.0.
        raise ConditionError(f"Value reference cannot be a boolean, got {raw!r}")
    if isinstance(raw, (int, float)):
        return ConstantRef(value=float(raw))
    if isinstance(raw, Mapping):
        if "indicator" not in raw:
            raise ConditionError(
                f"Value reference mapping must have an 'indicator' key, got {dict(raw)!r}"
            )
        name = raw["indicator"]
        if not isinstance(name, str):
            raise ConditionError(f"'indicator' must be a string, got {name!r}")
        params = {k: v for k, v in raw.items() if k != "indicator"}
        return IndicatorRef(name=name, params=params)
    raise ConditionError(
        f"Unrecognized value reference {raw!r} (expected 'close', a number, or a mapping)"
    )
