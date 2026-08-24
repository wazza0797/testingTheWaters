from __future__ import annotations

import pytest

from trading_platform.domain.errors import StrategyError
from trading_platform.strategies.examples.sma_crossover import SmaCrossoverStrategy
from trading_platform.strategies.loader import instantiate_strategy, load_strategy_class

_VALID_PATH = "trading_platform.strategies.examples.sma_crossover:SmaCrossoverStrategy"


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
            load_strategy_class("trading_platform.strategies.examples.sma_crossover:_STRATEGY_NAME")


class TestInstantiateStrategy:
    def test_instantiates_with_default_params(self) -> None:
        strategy = instantiate_strategy(_VALID_PATH)

        assert isinstance(strategy, SmaCrossoverStrategy)

    def test_instantiates_with_provided_params(self) -> None:
        strategy = instantiate_strategy(_VALID_PATH, params={"fast_period": 5, "slow_period": 20})

        assert isinstance(strategy, SmaCrossoverStrategy)
        assert strategy.fast_period == 5
        assert strategy.slow_period == 20

    def test_raises_strategy_error_on_bad_params(self) -> None:
        with pytest.raises(StrategyError, match="Failed to instantiate"):
            instantiate_strategy(_VALID_PATH, params={"not_a_real_param": 1})

    def test_none_params_is_equivalent_to_no_params(self) -> None:
        strategy = instantiate_strategy(_VALID_PATH, params=None)

        assert isinstance(strategy, SmaCrossoverStrategy)
