"""Composable condition trees (`all`/`any`/`not` over `compare`/
`compare_indicators`/`cross` leaves) for building entry/exit rules and
regime `when` predicates out of any mix of indicators — see the
composable-strategies milestone doc.

No arbitrary Python `eval`: conditions are parsed from plain YAML-loaded
mappings/lists into a small, walkable AST (`ast.py`), so a strategy config
can only ever express the shapes this package explicitly supports.
"""

from __future__ import annotations

from trading_platform.strategies.rules.ast import (
    AllOf,
    AnyOf,
    Compare,
    CompareIndicators,
    Condition,
    Cross,
    NotOf,
)
from trading_platform.strategies.rules.errors import ConditionError
from trading_platform.strategies.rules.evaluator import TriBool, evaluate
from trading_platform.strategies.rules.parser import (
    parse_condition,
    validate_condition_indicators,
)
from trading_platform.strategies.rules.values import CloseRef, ConstantRef, IndicatorRef, ValueSpec

__all__ = [
    "AllOf",
    "AnyOf",
    "CloseRef",
    "Compare",
    "CompareIndicators",
    "Condition",
    "ConditionError",
    "ConstantRef",
    "Cross",
    "IndicatorRef",
    "NotOf",
    "TriBool",
    "ValueSpec",
    "evaluate",
    "parse_condition",
    "validate_condition_indicators",
]
