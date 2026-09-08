from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from trading_platform.strategies.rules.values import ValueSpec

#: The comparison operators every `compare`/`compare_indicators` leaf may
#: use. Kept as plain strings (not an enum) so YAML authors write exactly
#: what they'd expect (`">="`, not `"GE"`); `parser.py` validates against
#: this set at parse time.
COMPARISON_OPS = (">", ">=", "<", "<=", "==")

#: The two crossing directions a `cross` leaf may detect.
CROSS_DIRECTIONS = ("above", "below")


@dataclass(frozen=True, slots=True)
class Compare:
    """Leaf: one indicator (or `close`/constant) compared against a fixed
    numeric `value` — e.g. `rsi(period=14) >= 45`.
    """

    left: ValueSpec
    op: str
    value: float

    def describe(self) -> str:
        return f"{self.left.describe()} {self.op} {self.value}"


@dataclass(frozen=True, slots=True)
class CompareIndicators:
    """Leaf: two value references compared against each other — e.g.
    `ema(period=50) > ema(period=200)`.
    """

    left: ValueSpec
    op: str
    right: ValueSpec

    def describe(self) -> str:
        return f"{self.left.describe()} {self.op} {self.right.describe()}"


@dataclass(frozen=True, slots=True)
class Cross:
    """Leaf: `left` crosses `direction` `right` **on this bar** — true only
    on the bar where the relationship flips (mirrors
    `SmaCrossoverStrategy`'s crossover discipline: `prev_left <= prev_right
    and curr_left > curr_right` for `"above"`, the mirror image for
    `"below"`). Needs at least one bar of history *before* the current one;
    always `None` ("not ready") on the very first bar.
    """

    left: ValueSpec
    right: ValueSpec
    direction: str

    def describe(self) -> str:
        return f"{self.left.describe()} crosses {self.direction} {self.right.describe()}"


@dataclass(frozen=True, slots=True)
class AllOf:
    """Combinator: AND of every child condition (Kleene 3-valued: `False`
    if any child is `False`, else `None` if any child is `None`
    ["not ready"], else `True`)."""

    children: tuple[Condition, ...]


@dataclass(frozen=True, slots=True)
class AnyOf:
    """Combinator: OR of every child condition (Kleene 3-valued: `True` if
    any child is `True`, else `None` if any child is `None`, else
    `False`)."""

    children: tuple[Condition, ...]


@dataclass(frozen=True, slots=True)
class NotOf:
    """Combinator: negation of one child condition. `None` ("not ready")
    stays `None` — negating "unknown" is still "unknown", never flipped
    into a spurious `True` (see `evaluator.py` for why this matters during
    indicator warmup).
    """

    child: Condition


#: A condition tree node: either a leaf (`Compare`/`CompareIndicators`/
#: `Cross`) or a combinator (`AllOf`/`AnyOf`/`NotOf`) whose children are
#: themselves `Condition`s. Built exclusively by `parser.py::parse_condition`
#: from nested YAML mappings/lists, so — structurally — it is always a tree,
#: never a graph: nothing in this module supports constructing a cycle.
Condition: TypeAlias = "Compare | CompareIndicators | Cross | AllOf | AnyOf | NotOf"
