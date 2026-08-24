from __future__ import annotations

import pytest

from trading_platform.domain.errors import StrategyError
from trading_platform.strategies.examples.sma_crossover import SmaCrossoverStrategy
from trading_platform.strategies.loader import (
    describe_strategy,
    instantiate_strategy,
    load_strategy_class,
)

_VALID_PATH = "trading_platform.strategies.examples.sma_crossover:SmaCrossoverStrategy"
_MISSING_ON_STOP_PATH = "tests.unit.strategies._fixtures:MissingOnStopStrategy"


class TestLoadStrategyClass:
    def test_resolves_a_valid_dotted_path(self) -> None:
        strategy_cls = load_strategy_class(_VALID_PATH)

        assert strategy_cls is SmaCrossoverStrategy

    def test_raises_when_path_has_no_colon(self) -> None:
        with pytest.raises(StrategyError, match="module:ClassName"):
            load_strategy_class("trading_platform.strategies.examples.sma_crossover")

    def test_raises_when_module_or_class_segment_is_empty(self) -> None:
        with pytest.raises(StrategyError, match="module:ClassName"):
            load_strategy_class(":SmaCrossoverStrategy")
        with pytest.raises(StrategyError, match="module:ClassName"):
            load_strategy_class("trading_platform.strategies.examples.sma_crossover:")

    def test_raises_when_module_does_not_exist(self) -> None:
        with pytest.raises(StrategyError, match="Could not import"):
            load_strategy_class("trading_platform.strategies.examples.does_not_exist:Foo")

    def test_raises_when_class_does_not_exist_on_module(self) -> None:
        with pytest.raises(StrategyError, match="no attribute"):
            load_strategy_class("trading_platform.strategies.examples.sma_crossover:DoesNotExist")

    def test_raises_when_attribute_is_not_a_class(self) -> None:
        with pytest.raises(StrategyError, match="does not resolve to a class"):
            load_strategy_class(
                "trading_platform.strategies.examples.sma_crossover:_PLACEHOLDER_STRATEGY_NAME"
            )


class TestInstantiateStrategy:
    def test_instantiates_with_default_params(self) -> None:
        strategy = instantiate_strategy(_VALID_PATH)

        assert isinstance(strategy, SmaCrossoverStrategy)

    def test_instantiates_with_provided_params(self) -> None:
        strategy = instantiate_strategy(_VALID_PATH, params={"fast_period": 5, "slow_period": 20})

        assert isinstance(strategy, SmaCrossoverStrategy)
        assert strategy.fast_period == 5
        assert strategy.slow_period == 20

    def test_raises_strategy_error_on_unknown_keyword_param(self) -> None:
        with pytest.raises(StrategyError, match="Failed to instantiate"):
            instantiate_strategy(_VALID_PATH, params={"not_a_real_param": 1})

    def test_raises_strategy_error_not_bare_value_error_on_invalid_param_values(self) -> None:
        # SmaCrossoverStrategy.__init__ raises a bare ValueError for this —
        # instantiate_strategy must normalize it to StrategyError, not let it
        # propagate as-is (regression test for a Bugbot finding on this PR).
        with pytest.raises(StrategyError, match="Failed to instantiate"):
            instantiate_strategy(_VALID_PATH, params={"fast_period": 30, "slow_period": 10})

    def test_none_params_is_equivalent_to_no_params(self) -> None:
        strategy = instantiate_strategy(_VALID_PATH, params=None)

        assert isinstance(strategy, SmaCrossoverStrategy)

    def test_raises_when_constructed_object_does_not_implement_istrategy(self) -> None:
        # Fails fast here with a clear message, instead of an AttributeError
        # surfacing deep inside StrategyHandler the first time it's used.
        with pytest.raises(StrategyError, match="does not implement IStrategy"):
            instantiate_strategy(_MISSING_ON_STOP_PATH)


class TestDescribeStrategy:
    def test_class_name_and_symbol_only_when_there_are_no_params(self) -> None:
        assert describe_strategy(_VALID_PATH, symbol="BTC/USDT") == "SmaCrossoverStrategy[BTC/USDT]"
        assert (
            describe_strategy(_VALID_PATH, symbol="BTC/USDT", params={})
            == "SmaCrossoverStrategy[BTC/USDT]"
        )

    def test_renders_params_sorted_by_key_for_determinism(self) -> None:
        name = describe_strategy(
            _VALID_PATH, symbol="BTC/USDT", params={"slow_period": 20, "fast_period": 5}
        )

        assert name == "SmaCrossoverStrategy[BTC/USDT](fast_period=5,slow_period=20)"

    def test_two_different_param_sets_produce_two_different_names(self) -> None:
        fast = describe_strategy(
            _VALID_PATH, symbol="BTC/USDT", params={"fast_period": 5, "slow_period": 20}
        )
        slow = describe_strategy(
            _VALID_PATH, symbol="BTC/USDT", params={"fast_period": 20, "slow_period": 60}
        )

        assert fast != slow

    def test_same_class_and_params_on_different_symbols_produce_different_names(self) -> None:
        btc = describe_strategy(
            _VALID_PATH, symbol="BTC/USDT", params={"fast_period": 5, "slow_period": 20}
        )
        eth = describe_strategy(
            _VALID_PATH, symbol="ETH/USDT", params={"fast_period": 5, "slow_period": 20}
        )

        assert btc != eth
        assert btc == "SmaCrossoverStrategy[BTC/USDT](fast_period=5,slow_period=20)"
        assert eth == "SmaCrossoverStrategy[ETH/USDT](fast_period=5,slow_period=20)"
