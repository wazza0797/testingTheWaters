from __future__ import annotations

from enum import StrEnum

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from trading_platform.domain.errors import ConfigurationError


class Environment(StrEnum):
    PAPER = "paper"
    LIVE = "live"
    BACKTEST = "backtest"


class Settings(BaseSettings):
    """Environment-variable-backed secrets and deployment knobs.

    Strategy/symbol/timeframe/backtest parameters never live here — those
    belong in `config/*.yaml`, loaded via `config/loader.py`. This class exists
    only for secrets (API keys, Telegram token) and the small set of
    operational toggles that must be settable without editing a committed
    file (see the Docker environment variable table in `docs/architecture.md`).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Environment = Field(default=Environment.PAPER, validation_alias="ENV")
    data_dir: str = Field(default="data", validation_alias="DATA_DIR")

    binance_api_key: str | None = Field(default=None, validation_alias="BINANCE_API_KEY")
    binance_api_secret: str | None = Field(default=None, validation_alias="BINANCE_API_SECRET")

    telegram_bot_token: str | None = Field(default=None, validation_alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str | None = Field(default=None, validation_alias="TELEGRAM_CHAT_ID")
    discord_webhook_url: str | None = Field(default=None, validation_alias="DISCORD_WEBHOOK_URL")

    live_trading_enabled: bool = Field(default=False, validation_alias="LIVE_TRADING_ENABLED")

    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    log_format: str = Field(default="text", validation_alias="LOG_FORMAT")

    metrics_port: int = Field(default=9090, validation_alias="METRICS_PORT")
    health_port: int = Field(default=8080, validation_alias="HEALTH_PORT")
    observability_enabled: bool = Field(default=True, validation_alias="OBSERVABILITY_ENABLED")

    def require_live_trading_confirmed(self) -> None:
        """Milestone 8 double-gate: `ENV=live` alone must never be sufficient
        to place real orders — `LIVE_TRADING_ENABLED=true` must also be set.
        """
        if self.environment == Environment.LIVE and not self.live_trading_enabled:
            raise ConfigurationError(
                "ENV=live requires LIVE_TRADING_ENABLED=true to be set explicitly."
            )
