from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from trading_platform.strategies.context import DefaultStrategyContext
from trading_platform.strategies.rules.ast import AllOf, AnyOf, Compare, Cross, NotOf
from trading_platform.strategies.rules.evaluator import evaluate
from trading_platform.strategies.rules.parser import parse_condition
from trading_platform.strategies.rules.values import CloseRef, ConstantRef, IndicatorRef


def _bars(make_bar, closes: list[str]) -> list:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        make_bar(timestamp=start + timedelta(hours=i), open_=c, high=c, low=c, close=c)
        for i, c in enumerate(closes)
    ]


@pytest.fixture
def ctx() -> DefaultStrategyContext:
    return DefaultStrategyContext(symbol="BTC/USDT", timeframe="1h")


class TestCompareLeaf:
    def test_true_when_condition_holds(self, make_bar, ctx) -> None:
        bars = _bars(make_bar, ["10", "20", "30"])
        node = Compare(left=IndicatorRef("sma", {"period": 2}), op=">", value=15.0)
        assert evaluate(node, bars, ctx) is True

    def test_false_when_condition_does_not_hold(self, make_bar, ctx) -> None:
        bars = _bars(make_bar, ["10", "20", "30"])
        node = Compare(left=IndicatorRef("sma", {"period": 2}), op="<", value=15.0)
        assert evaluate(node, bars, ctx) is False

    def test_none_during_warmup(self, make_bar, ctx) -> None:
        bars = _bars(make_bar, ["10"])
        node = Compare(left=IndicatorRef("sma", {"period": 5}), op=">", value=1.0)
        assert evaluate(node, bars, ctx) is None

    def test_close_ref_as_left(self, make_bar, ctx) -> None:
        bars = _bars(make_bar, ["10", "20", "30"])
        node = Compare(left=CloseRef(), op="==", value=30.0)
        assert evaluate(node, bars, ctx) is True

    def test_trace_records_leaf_result(self, make_bar, ctx) -> None:
        bars = _bars(make_bar, ["10", "20", "30"])
        node = Compare(left=IndicatorRef("sma", {"period": 2}), op=">", value=15.0)
        trace: dict = {}
        evaluate(node, bars, ctx, trace)
        assert trace == {"sma(period=2) > 15.0": True}


class TestCompareIndicatorsLeaf:
    def test_compares_two_indicators(self, make_bar, ctx) -> None:
        from trading_platform.strategies.rules.ast import CompareIndicators

        bars = _bars(make_bar, [str(100 + i) for i in range(10)])
        node = CompareIndicators(
            left=IndicatorRef("sma", {"period": 2}),
            op=">",
            right=IndicatorRef("sma", {"period": 5}),
        )
        # Rising series: fast SMA > slow SMA.
        assert evaluate(node, bars, ctx) is True

    def test_none_when_either_side_is_nan(self, make_bar, ctx) -> None:
        from trading_platform.strategies.rules.ast import CompareIndicators

        bars = _bars(make_bar, ["10", "20"])
        node = CompareIndicators(
            left=IndicatorRef("sma", {"period": 2}),
            op=">",
            right=IndicatorRef("sma", {"period": 10}),
        )
        assert evaluate(node, bars, ctx) is None


class TestCrossLeaf:
    def test_detects_upward_cross_only_on_the_crossing_bar(self, make_bar, ctx) -> None:
        # fast=2, slow=3 SMA — same worked example as SmaCrossoverStrategy's
        # own tests: golden cross at index 5.
        closes = ["100", "100", "100", "100", "100", "200", "50", "50", "50", "50", "50"]
        bars = _bars(make_bar, closes)
        node = Cross(
            left=IndicatorRef("sma", {"period": 2}),
            right=IndicatorRef("sma", {"period": 3}),
            direction="above",
        )

        results = [evaluate(node, bars[: i + 1], ctx) for i in range(len(bars))]

        assert results[5] is True
        assert all(r is not True for i, r in enumerate(results) if i != 5)

    def test_none_on_the_very_first_bar(self, make_bar, ctx) -> None:
        bars = _bars(make_bar, ["100"])
        node = Cross(left=CloseRef(), right=ConstantRef(50.0), direction="above")
        assert evaluate(node, bars, ctx) is None

    def test_close_crosses_above_constant(self, make_bar, ctx) -> None:
        bars = _bars(make_bar, ["40", "60"])
        node = Cross(left=CloseRef(), right=ConstantRef(50.0), direction="above")
        assert evaluate(node, bars, ctx) is True

    def test_close_crosses_below_constant(self, make_bar, ctx) -> None:
        bars = _bars(make_bar, ["60", "40"])
        node = Cross(left=CloseRef(), right=ConstantRef(50.0), direction="below")
        assert evaluate(node, bars, ctx) is True


class TestCombinators:
    def test_all_of_is_true_only_when_every_child_is_true(self, make_bar, ctx) -> None:
        bars = _bars(make_bar, ["10", "20", "30"])
        true_leaf = Compare(left=CloseRef(), op="==", value=30.0)
        false_leaf = Compare(left=CloseRef(), op="==", value=99.0)

        assert evaluate(AllOf((true_leaf, true_leaf)), bars, ctx) is True
        assert evaluate(AllOf((true_leaf, false_leaf)), bars, ctx) is False

    def test_all_of_is_false_if_any_child_false_even_with_a_none_child(self, make_bar, ctx) -> None:
        bars = _bars(make_bar, ["10"])
        false_leaf = Compare(left=CloseRef(), op="==", value=99.0)
        none_leaf = Compare(left=IndicatorRef("sma", {"period": 5}), op=">", value=1.0)

        assert evaluate(AllOf((false_leaf, none_leaf)), bars, ctx) is False

    def test_all_of_is_none_when_no_child_is_false_but_one_is_none(self, make_bar, ctx) -> None:
        bars = _bars(make_bar, ["10"])
        true_leaf = Compare(left=CloseRef(), op="==", value=10.0)
        none_leaf = Compare(left=IndicatorRef("sma", {"period": 5}), op=">", value=1.0)

        assert evaluate(AllOf((true_leaf, none_leaf)), bars, ctx) is None

    def test_any_of_is_true_if_any_child_true_even_with_a_none_child(self, make_bar, ctx) -> None:
        bars = _bars(make_bar, ["10"])
        true_leaf = Compare(left=CloseRef(), op="==", value=10.0)
        none_leaf = Compare(left=IndicatorRef("sma", {"period": 5}), op=">", value=1.0)

        assert evaluate(AnyOf((true_leaf, none_leaf)), bars, ctx) is True

    def test_any_of_is_none_when_no_child_is_true_but_one_is_none(self, make_bar, ctx) -> None:
        bars = _bars(make_bar, ["10"])
        false_leaf = Compare(left=CloseRef(), op="==", value=99.0)
        none_leaf = Compare(left=IndicatorRef("sma", {"period": 5}), op=">", value=1.0)

        assert evaluate(AnyOf((false_leaf, none_leaf)), bars, ctx) is None

    def test_any_of_is_false_when_every_child_is_false(self, make_bar, ctx) -> None:
        bars = _bars(make_bar, ["10"])
        false_leaf = Compare(left=CloseRef(), op="==", value=99.0)
        assert evaluate(AnyOf((false_leaf, false_leaf)), bars, ctx) is False

    def test_not_of_negates_a_known_result(self, make_bar, ctx) -> None:
        bars = _bars(make_bar, ["10"])
        true_leaf = Compare(left=CloseRef(), op="==", value=10.0)
        assert evaluate(NotOf(true_leaf), bars, ctx) is False

    def test_not_of_a_none_child_stays_none_not_true(self, make_bar, ctx) -> None:
        """The whole point of Kleene 3-valued logic here: negating an
        indicator that hasn't warmed up must not silently become a
        confident `True` signal."""
        bars = _bars(make_bar, ["10"])
        none_leaf = Compare(left=IndicatorRef("sma", {"period": 5}), op="<", value=1.0)
        assert evaluate(none_leaf, bars, ctx) is None
        assert evaluate(NotOf(none_leaf), bars, ctx) is None

    def test_nested_any_within_all(self, make_bar, ctx) -> None:
        bars = _bars(make_bar, ["10", "20", "30"])
        inner_any = AnyOf(
            (
                Compare(left=CloseRef(), op="==", value=99.0),  # False
                Compare(left=CloseRef(), op="==", value=30.0),  # True
            )
        )
        outer_all = AllOf((inner_any, Compare(left=CloseRef(), op=">", value=0.0)))
        assert evaluate(outer_all, bars, ctx) is True


class TestParsedRecipeEndToEnd:
    def test_two_volume_or_plus_volatility_and_recipe(self, make_bar, ctx) -> None:
        start = datetime(2024, 1, 1, tzinfo=UTC)
        bars = [
            make_bar(
                timestamp=start + timedelta(hours=i),
                open_=str(100 + i),
                high=str(105 + i),
                low=str(95 + i),
                close=str(100 + i),
                volume=str(100 if i < 25 else 500),  # spike at the end
            )
            for i in range(30)
        ]
        condition = parse_condition(
            {
                "all": [
                    {
                        "any": [
                            {
                                "compare": {
                                    "indicator": "rel_volume",
                                    "period": 5,
                                    "op": ">=",
                                    "value": 1.5,
                                }
                            },
                            {
                                "compare": {
                                    "indicator": "volume_breakout",
                                    "period": 5,
                                    "op": "==",
                                    "value": 1,
                                }
                            },
                        ]
                    },
                    {"compare": {"indicator": "atr_pct", "period": 5, "op": ">=", "value": 0.0}},
                ]
            }
        )
        # At the spike's first bar (index 25), the rolling volume window
        # still spans mostly-calm history, so rel_volume/volume_breakout
        # spike relative to it; by the last bar the window is entirely
        # inside the spike again, so check right at the transition.
        assert evaluate(condition, bars[:26], ctx) is True
