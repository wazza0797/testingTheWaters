from __future__ import annotations

import pytest

from trading_platform.config.loader import load_config
from trading_platform.config.settings import Environment, Settings
from trading_platform.domain.errors import ConfigurationError, ExchangeAdapterError
from trading_platform.exchanges.binance.adapter import BinanceAdapter
from trading_platform.exchanges.factory import build_exchange_adapter


class TestExchangeFactory:
    def test_unknown_exchange_raises(self) -> None:
        settings = Settings(_env_file=None)
        with pytest.raises(ConfigurationError, match="Unsupported exchange"):
            build_exchange_adapter("trading212", Environment.DEMO, settings)

    def test_binance_paper_returns_adapter(self) -> None:
        settings = Settings(_env_file=None)
        adapter = build_exchange_adapter("binance", Environment.PAPER, settings)
        assert isinstance(adapter, BinanceAdapter)

    def test_binance_demo_requires_demo_keys(self) -> None:
        settings = Settings(
            _env_file=None,
            BINANCE_DEMO_API_KEY=None,
            BINANCE_DEMO_API_SECRET=None,
        )
        with pytest.raises(ExchangeAdapterError, match="BINANCE_DEMO"):
            build_exchange_adapter("binance", Environment.DEMO, settings)

    def test_binance_live_not_implemented(self) -> None:
        settings = Settings(_env_file=None)
        with pytest.raises(ConfigurationError, match="not implemented"):
            build_exchange_adapter("binance", Environment.LIVE, settings)


class TestDemoConfigOverlay:
    def test_demo_yaml_loads(self) -> None:
        config = load_config(overlay="demo")
        assert config.demo.state_file == "demo_state.json"
        assert config.trading.exchange == "binance"


class TestEnvironmentDemo:
    def test_demo_env_parses(self) -> None:
        settings = Settings(_env_file=None, ENV="demo")
        assert settings.environment == Environment.DEMO
