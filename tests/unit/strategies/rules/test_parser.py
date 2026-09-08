from __future__ import annotations

import pytest

from trading_platform.strategies.rules.ast import (
    AllOf,
    AnyOf,
    Compare,
    CompareIndicators,
    Cross,
    NotOf,
)
from trading_platform.strategies.rules.errors import ConditionError
from trading_platform.strategies.rules.parser import (
    collect_indicator_names,
    parse_condition,
    validate_condition_indicators,
)
from trading_platform.strategies.rules.values import CloseRef, ConstantRef, IndicatorRef


class TestParseCompare:
    def test_parses_indicator_op_value(self) -> None:
        node = parse_condition(
            {"compare": {"indicator": "rsi", "period": 14, "op": ">=", "value": 45}}
        )
        assert isinstance(node, Compare)
        assert node.left == IndicatorRef(name="rsi", params={"period": 14})
        assert node.op == ">="
        assert node.value == 45.0

    def test_missing_indicator_raises(self) -> None:
        with pytest.raises(ConditionError, match="indicator"):
            parse_condition({"compare": {"op": ">", "value": 1}})

    def test_missing_op_or_value_raises(self) -> None:
        with pytest.raises(ConditionError, match="op"):
            parse_condition({"compare": {"indicator": "rsi"}})

    def test_unknown_op_raises(self) -> None:
        with pytest.raises(ConditionError, match="Unknown comparison operator"):
            parse_condition({"compare": {"indicator": "rsi", "op": "~=", "value": 1}})

    def test_non_numeric_value_raises(self) -> None:
        with pytest.raises(ConditionError, match="number"):
            parse_condition({"compare": {"indicator": "rsi", "op": ">", "value": "high"}})

    def test_boolean_value_raises(self) -> None:
        with pytest.raises(ConditionError, match="number"):
            parse_condition({"compare": {"indicator": "rsi", "op": ">", "value": True}})


class TestParseCompareIndicators:
    def test_parses_left_right_op(self) -> None:
        node = parse_condition(
            {
                "compare_indicators": {
                    "left": {"indicator": "ema", "period": 50},
                    "right": {"indicator": "ema", "period": 200},
                    "op": ">",
                }
            }
        )
        assert isinstance(node, CompareIndicators)
        assert node.left == IndicatorRef(name="ema", params={"period": 50})
        assert node.right == IndicatorRef(name="ema", params={"period": 200})
        assert node.op == ">"

    def test_missing_keys_raises(self) -> None:
        with pytest.raises(ConditionError, match="compare_indicators"):
            parse_condition({"compare_indicators": {"left": {"indicator": "ema"}}})


class TestParseCross:
    def test_parses_close_vs_indicator(self) -> None:
        node = parse_condition(
            {
                "cross": {
                    "left": "close",
                    "right": {"indicator": "donchian_upper", "period": 20},
                    "direction": "above",
                }
            }
        )
        assert isinstance(node, Cross)
        assert node.left == CloseRef()
        assert node.right == IndicatorRef(name="donchian_upper", params={"period": 20})
        assert node.direction == "above"

    def test_rejects_bad_direction(self) -> None:
        with pytest.raises(ConditionError, match="direction"):
            parse_condition(
                {"cross": {"left": "close", "right": {"indicator": "ema"}, "direction": "up"}}
            )

    def test_accepts_numeric_operand(self) -> None:
        node = parse_condition(
            {"cross": {"left": {"indicator": "rsi"}, "right": 50, "direction": "above"}}
        )
        assert isinstance(node, Cross)
        assert node.right == ConstantRef(value=50.0)


class TestParseCombinators:
    def test_all_of_nested_children(self) -> None:
        node = parse_condition(
            {
                "all": [
                    {"compare": {"indicator": "rsi", "op": ">", "value": 50}},
                    {"compare": {"indicator": "adx", "op": ">", "value": 20}},
                ]
            }
        )
        assert isinstance(node, AllOf)
        assert len(node.children) == 2

    def test_any_of_nested_children(self) -> None:
        node = parse_condition(
            {
                "any": [
                    {"compare": {"indicator": "rsi", "op": ">", "value": 50}},
                    {"compare": {"indicator": "adx", "op": ">", "value": 20}},
                ]
            }
        )
        assert isinstance(node, AnyOf)
        assert len(node.children) == 2

    def test_not_of_single_child(self) -> None:
        node = parse_condition({"not": {"compare": {"indicator": "rsi", "op": ">", "value": 50}}})
        assert isinstance(node, NotOf)
        assert isinstance(node.child, Compare)

    def test_mixed_any_of_all(self) -> None:
        node = parse_condition(
            {
                "all": [
                    {
                        "any": [
                            {"compare": {"indicator": "rel_volume", "op": ">=", "value": 1.5}},
                            {
                                "compare": {
                                    "indicator": "volume_breakout",
                                    "op": "==",
                                    "value": 1,
                                }
                            },
                        ]
                    },
                    {"compare": {"indicator": "bb_width", "op": ">=", "value": 0.02}},
                ]
            }
        )
        assert isinstance(node, AllOf)
        assert isinstance(node.children[0], AnyOf)
        assert isinstance(node.children[1], Compare)

    def test_all_requires_non_empty_list(self) -> None:
        with pytest.raises(ConditionError, match="non-empty"):
            parse_condition({"all": []})

    def test_any_requires_a_list(self) -> None:
        with pytest.raises(ConditionError, match="non-empty"):
            parse_condition({"any": {"compare": {"indicator": "rsi", "op": ">", "value": 1}}})


class TestParseTopLevelShape:
    def test_rejects_multi_key_mapping(self) -> None:
        with pytest.raises(ConditionError, match="single-key mapping"):
            parse_condition({"all": [], "any": []})

    def test_rejects_unknown_key(self) -> None:
        with pytest.raises(ConditionError, match="Unknown condition type"):
            parse_condition({"weird": {}})

    def test_rejects_non_mapping(self) -> None:
        with pytest.raises(ConditionError, match="single-key mapping"):
            parse_condition("not a mapping")


class TestCollectIndicatorNames:
    def test_collects_from_nested_tree(self) -> None:
        node = parse_condition(
            {
                "all": [
                    {
                        "any": [
                            {"compare": {"indicator": "rel_volume", "op": ">=", "value": 1.5}},
                            {"compare": {"indicator": "bb_width", "op": ">=", "value": 0.02}},
                        ]
                    },
                    {
                        "compare_indicators": {
                            "left": {"indicator": "ema", "period": 50},
                            "right": {"indicator": "ema", "period": 200},
                            "op": ">",
                        }
                    },
                    {
                        "not": {
                            "cross": {
                                "left": "close",
                                "right": {"indicator": "donchian_lower"},
                                "direction": "below",
                            }
                        }
                    },
                ]
            }
        )
        assert collect_indicator_names(node) == {
            "rel_volume",
            "bb_width",
            "ema",
            "donchian_lower",
        }


class TestValidateConditionIndicators:
    def test_passes_when_all_names_known(self) -> None:
        node = parse_condition({"compare": {"indicator": "rsi", "op": ">", "value": 50}})
        validate_condition_indicators(node, available=["rsi", "adx"])  # no raise

    def test_raises_listing_unknown_names(self) -> None:
        node = parse_condition(
            {
                "all": [
                    {"compare": {"indicator": "rsi", "op": ">", "value": 50}},
                    {"compare": {"indicator": "not_a_real_indicator", "op": ">", "value": 1}},
                ]
            }
        )
        with pytest.raises(ConditionError, match="not_a_real_indicator"):
            validate_condition_indicators(node, available=["rsi"])
