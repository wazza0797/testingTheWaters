from __future__ import annotations

from pathlib import Path

import pytest

from trading_platform.config.loader import _deep_merge, load_config
from trading_platform.domain.errors import ConfigurationError


class TestDeepMerge:
    def test_override_wins_for_scalar_values(self) -> None:
        result = _deep_merge({"a": 1, "b": 2}, {"b": 3})
        assert result == {"a": 1, "b": 3}

    def test_nested_dicts_are_merged_not_replaced(self) -> None:
        base = {"trading": {"symbol": "BTC/USDT", "timeframe": "1h"}}
        override = {"trading": {"timeframe": "4h"}}
        result = _deep_merge(base, override)
        assert result == {"trading": {"symbol": "BTC/USDT", "timeframe": "4h"}}


class TestLoadConfig:
    def test_loads_repo_config_directory_with_expected_defaults(self) -> None:
        config = load_config(config_dir=Path("config"))

        assert config.trading.symbol == "BTC/USDT"
        assert config.observability.metrics_port == 9090
        assert config.backtest.latency_bars == 1

    def test_missing_directory_falls_back_to_model_defaults(self, tmp_path: Path) -> None:
        config = load_config(config_dir=tmp_path / "does-not-exist")

        assert config.trading.exchange == "binance"
        assert config.observability.enabled is True

    def test_overlay_is_deep_merged_over_defaults(self, tmp_path: Path) -> None:
        (tmp_path / "default.yaml").write_text("trading:\n  symbol: BTC/USDT\n  timeframe: 1h\n")
        (tmp_path / "backtest_overlay.yaml").write_text("trading:\n  timeframe: 4h\n")

        config = load_config(config_dir=tmp_path, overlay="backtest_overlay")

        assert config.trading.symbol == "BTC/USDT"
        assert config.trading.timeframe == "4h"

    def test_non_mapping_yaml_raises_configuration_error(self, tmp_path: Path) -> None:
        (tmp_path / "default.yaml").write_text("- not\n- a\n- mapping\n")

        with pytest.raises(ConfigurationError):
            load_config(config_dir=tmp_path)

    def test_strategy_defaults_to_no_path_and_empty_params(self) -> None:
        config = load_config(config_dir=Path("config"))

        assert config.strategy.path is None
        assert config.strategy.params == {}

    def test_strategy_section_is_read_from_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "default.yaml").write_text(
            "strategy:\n"
            '  path: "trading_platform.strategies.examples.sma_crossover:SmaCrossoverStrategy"\n'
            "  params:\n"
            "    fast_period: 5\n"
            "    slow_period: 20\n"
        )

        config = load_config(config_dir=tmp_path)

        assert config.strategy.path == (
            "trading_platform.strategies.examples.sma_crossover:SmaCrossoverStrategy"
        )
        assert config.strategy.params == {"fast_period": 5, "slow_period": 20}
