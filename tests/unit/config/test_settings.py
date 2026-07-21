from __future__ import annotations

import pytest

from trading_platform.config.settings import Environment, Settings
from trading_platform.domain.errors import ConfigurationError


class TestSettings:
    def test_defaults_are_safe_for_paper_trading(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)
        settings = Settings(_env_file=None)

        assert settings.environment is Environment.PAPER
        assert settings.live_trading_enabled is False
        assert settings.binance_api_key is None

    def test_env_vars_override_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("METRICS_PORT", "9999")
        settings = Settings(_env_file=None)

        assert settings.log_level == "DEBUG"
        assert settings.metrics_port == 9999

    def test_live_without_confirmation_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENV", "live")
        monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
        settings = Settings(_env_file=None)

        with pytest.raises(ConfigurationError, match="LIVE_TRADING_ENABLED"):
            settings.require_live_trading_confirmed()

    def test_live_with_confirmation_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENV", "live")
        monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
        settings = Settings(_env_file=None)

        settings.require_live_trading_confirmed()  # must not raise

    def test_paper_mode_never_requires_confirmation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENV", "paper")
        monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)
        settings = Settings(_env_file=None)

        settings.require_live_trading_confirmed()  # must not raise
