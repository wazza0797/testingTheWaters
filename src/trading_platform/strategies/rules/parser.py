from __future__ import annotations

from collections.abc import Collection, Mapping

from trading_platform.strategies.rules.ast import (
    COMPARISON_OPS,
    CROSS_DIRECTIONS,
    AllOf,
    AnyOf,
    Compare,
    CompareIndicators,
    Condition,
    Cross,
    NotOf,
)
from trading_platform.strategies.rules.errors import ConditionError
from trading_platform.strategies.rules.values import IndicatorRef, parse_value_spec


def parse_condition(raw: object) -> Condition:
    """Parse one YAML-loaded condition node into a `Condition` tree.

    Every node is a single-key mapping: `all`/`any` (list of nested
    conditions), `not` (one nested condition), or a leaf (`compare`/
    `compare_indicators`/`cross`, each a mapping of leaf-specific fields —
    see the module docstrings on `ast.py`'s dataclasses and the
    composable-strategies milestone doc for worked YAML examples).

    Raises `ConditionError` (a `StrategyError`) for any unrecognized shape,
    missing field, or unknown combinator/leaf name — always at parse time
    (strategy construction), never mid-run.
    """
    if not isinstance(raw, Mapping) or len(raw) != 1:
        raise ConditionError(
            f"A condition node must be a single-key mapping (e.g. {{'all': [...]}} "
            f"or {{'compare': {{...}}}}), got {raw!r}"
        )
    ((key, value),) = raw.items()
    if key == "all":
        return AllOf(_parse_condition_list(value, "all"))
    if key == "any":
        return AnyOf(_parse_condition_list(value, "any"))
    if key == "not":
        return NotOf(parse_condition(value))
    if key == "compare":
        return _parse_compare(value)
    if key == "compare_indicators":
        return _parse_compare_indicators(value)
    if key == "cross":
        return _parse_cross(value)
    raise ConditionError(
        f"Unknown condition type {key!r}. Expected one of: "
        "all, any, not, compare, compare_indicators, cross"
    )


def _parse_condition_list(value: object, key: str) -> tuple[Condition, ...]:
    if not isinstance(value, list) or len(value) == 0:
        raise ConditionError(f"'{key}' requires a non-empty list of condition nodes, got {value!r}")
    return tuple(parse_condition(child) for child in value)


def _parse_op(raw: object) -> str:
    if raw not in COMPARISON_OPS:
        raise ConditionError(
            f"Unknown comparison operator {raw!r}. Expected one of: {COMPARISON_OPS}"
        )
    return str(raw)


def _parse_compare(raw: object) -> Compare:
    if not isinstance(raw, Mapping):
        raise ConditionError(f"'compare' requires a mapping, got {raw!r}")
    if "indicator" not in raw:
        raise ConditionError(f"'compare' requires an 'indicator' key, got {dict(raw)!r}")
    if "op" not in raw or "value" not in raw:
        raise ConditionError(f"'compare' requires 'op' and 'value' keys, got {dict(raw)!r}")

    name = raw["indicator"]
    if not isinstance(name, str):
        raise ConditionError(f"'indicator' must be a string, got {name!r}")
    params = {k: v for k, v in raw.items() if k not in {"indicator", "op", "value"}}
    op = _parse_op(raw["op"])
    value = raw["value"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConditionError(f"'compare' value must be a number, got {value!r}")

    return Compare(left=IndicatorRef(name=name, params=params), op=op, value=float(value))


def _parse_compare_indicators(raw: object) -> CompareIndicators:
    if not isinstance(raw, Mapping):
        raise ConditionError(f"'compare_indicators' requires a mapping, got {raw!r}")
    if "left" not in raw or "right" not in raw or "op" not in raw:
        raise ConditionError(
            f"'compare_indicators' requires 'left', 'right', and 'op' keys, got {dict(raw)!r}"
        )
    return CompareIndicators(
        left=parse_value_spec(raw["left"]),
        op=_parse_op(raw["op"]),
        right=parse_value_spec(raw["right"]),
    )


def _parse_cross(raw: object) -> Cross:
    if not isinstance(raw, Mapping):
        raise ConditionError(f"'cross' requires a mapping, got {raw!r}")
    if "left" not in raw or "right" not in raw or "direction" not in raw:
        raise ConditionError(
            f"'cross' requires 'left', 'right', and 'direction' keys, got {dict(raw)!r}"
        )
    direction = raw["direction"]
    if direction not in CROSS_DIRECTIONS:
        raise ConditionError(
            f"'cross' direction must be one of {CROSS_DIRECTIONS}, got {direction!r}"
        )
    return Cross(
        left=parse_value_spec(raw["left"]),
        right=parse_value_spec(raw["right"]),
        direction=str(direction),
    )


def collect_indicator_names(condition: Condition) -> set[str]:
    """Every `IndicatorRef.name` referenced anywhere in `condition`'s tree
    — used by `validate_condition_indicators` to check every name exists in
    the registry before a strategy ever sees a live bar.
    """
    names: set[str] = set()
    _collect_into(condition, names)
    return names


def _collect_into(condition: Condition, names: set[str]) -> None:
    if isinstance(condition, Compare):
        _collect_value_spec(condition.left, names)
    elif isinstance(condition, (CompareIndicators, Cross)):
        _collect_value_spec(condition.left, names)
        _collect_value_spec(condition.right, names)
    elif isinstance(condition, (AllOf, AnyOf)):
        for child in condition.children:
            _collect_into(child, names)
    elif isinstance(condition, NotOf):
        _collect_into(condition.child, names)
    else:  # pragma: no cover — exhaustive over Condition's union members
        raise AssertionError(f"unhandled condition type {type(condition)!r}")


def _collect_value_spec(spec: object, names: set[str]) -> None:
    if isinstance(spec, IndicatorRef):
        names.add(spec.name)


def validate_condition_indicators(condition: Condition, available: Collection[str]) -> None:
    """Raise `ConditionError` listing every indicator name referenced in
    `condition` that is not in `available` (typically
    `IndicatorRegistry.available()`). Called once at strategy construct
    time so a typo'd indicator name fails immediately with every offending
    name, rather than surfacing as a `KeyError` deep in the first `on_bar`.
    """
    unknown = sorted(collect_indicator_names(condition) - set(available))
    if unknown:
        raise ConditionError(
            f"Unknown indicator name(s) {unknown} referenced in condition tree. "
            f"Available: {sorted(available)}"
        )
