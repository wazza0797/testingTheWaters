from __future__ import annotations

import math
from collections.abc import Callable, MutableMapping, Sequence
from typing import TypeAlias

from trading_platform.domain.models.bar import Bar
from trading_platform.domain.ports.strategy import StrategyContext
from trading_platform.strategies.rules.ast import (
    AllOf,
    AnyOf,
    Compare,
    CompareIndicators,
    Condition,
    Cross,
    NotOf,
)
from trading_platform.strategies.rules.values import CloseRef, ConstantRef, IndicatorRef, ValueSpec

#: Kleene 3-valued result: `True`/`False` are real, known outcomes; `None`
#: means "not enough history yet" (an operand was `NaN`, or a `cross` was
#: asked for on the very first bar). Combinators propagate `None` correctly
#: (see `AllOf`/`AnyOf`/`NotOf` below) instead of silently treating
#: "unknown" as a real `False` — see the composable-strategies milestone doc
#: for why this matters for `not` during indicator warmup.
TriBool: TypeAlias = "bool | None"

_OPS: dict[str, Callable[[float, float], bool]] = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
}


def evaluate(
    condition: Condition,
    bars: Sequence[Bar],
    ctx: StrategyContext,
    trace: MutableMapping[str, TriBool] | None = None,
) -> TriBool:
    """Evaluate `condition` against `bars` (the strategy's current bar
    window, most recent bar last) and `ctx` (for indicator lookups).

    If `trace` is given, every leaf (`Compare`/`CompareIndicators`/`Cross`)
    records its own `describe()` -> result into it — this is what
    `RuleStrategy`/`RegimeRouterStrategy` attach to `Signal.metadata` so a
    fired signal shows exactly which leaves were true/false/not-ready.
    """
    if isinstance(condition, Compare):
        left = _resolve(condition.left, bars, ctx)
        result: TriBool = None if math.isnan(left) else _OPS[condition.op](left, condition.value)
        _record(trace, condition, result)
        return result

    if isinstance(condition, CompareIndicators):
        left = _resolve(condition.left, bars, ctx)
        right = _resolve(condition.right, bars, ctx)
        result = (
            None if (math.isnan(left) or math.isnan(right)) else _OPS[condition.op](left, right)
        )
        _record(trace, condition, result)
        return result

    if isinstance(condition, Cross):
        result = _evaluate_cross(condition, bars, ctx)
        _record(trace, condition, result)
        return result

    if isinstance(condition, AllOf):
        results = [evaluate(child, bars, ctx, trace) for child in condition.children]
        if any(r is False for r in results):
            return False
        if any(r is None for r in results):
            return None
        return True

    if isinstance(condition, AnyOf):
        results = [evaluate(child, bars, ctx, trace) for child in condition.children]
        if any(r is True for r in results):
            return True
        if any(r is None for r in results):
            return None
        return False

    if isinstance(condition, NotOf):
        child_result = evaluate(condition.child, bars, ctx, trace)
        return None if child_result is None else (not child_result)

    raise AssertionError(f"unhandled condition type {type(condition)!r}")  # pragma: no cover


def _evaluate_cross(condition: Cross, bars: Sequence[Bar], ctx: StrategyContext) -> TriBool:
    if len(bars) < 2:
        return None
    curr_left = _resolve(condition.left, bars, ctx)
    curr_right = _resolve(condition.right, bars, ctx)
    prev_left = _resolve(condition.left, bars[:-1], ctx)
    prev_right = _resolve(condition.right, bars[:-1], ctx)
    if any(math.isnan(v) for v in (curr_left, curr_right, prev_left, prev_right)):
        return None
    if condition.direction == "above":
        return prev_left <= prev_right and curr_left > curr_right
    return prev_left >= prev_right and curr_left < curr_right  # "below"


def _resolve(spec: ValueSpec, bars: Sequence[Bar], ctx: StrategyContext) -> float:
    if isinstance(spec, IndicatorRef):
        return ctx.indicator(spec.name, bars, **spec.params)
    if isinstance(spec, CloseRef):
        return float(bars[-1].close) if bars else float("nan")
    if isinstance(spec, ConstantRef):
        return spec.value
    raise AssertionError(f"unhandled value spec type {type(spec)!r}")  # pragma: no cover


def _record(
    trace: MutableMapping[str, TriBool] | None,
    condition: Compare | CompareIndicators | Cross,
    result: TriBool,
) -> None:
    if trace is not None:
        trace[condition.describe()] = result
