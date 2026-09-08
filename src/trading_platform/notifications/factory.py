from __future__ import annotations

import logging

from trading_platform.config.settings import Environment, Settings
from trading_platform.domain.ports.notification import INotifier
from trading_platform.notifications.composite import CompositeNotifier
from trading_platform.notifications.console import ConsoleNotifier
from trading_platform.notifications.discord import DiscordNotifier
from trading_platform.notifications.telegram import TelegramNotifier

logger = logging.getLogger(__name__)


def discord_webhook_for(settings: Settings) -> str | None:
    """Pick the Discord webhook for the active `ENV` — no cross-mode fallback.

    - `ENV=demo` → `DISCORD_DEMO_WEBHOOK_URL`
    - `ENV=live` → `DISCORD_LIVE_WEBHOOK_URL`
    - paper / backtest / other → `DISCORD_WEBHOOK_URL` (local / simulation)
    """
    if settings.environment == Environment.DEMO:
        return settings.discord_demo_webhook_url
    if settings.environment == Environment.LIVE:
        return settings.discord_live_webhook_url
    return settings.discord_webhook_url


def build_notifier(settings: Settings) -> INotifier:
    """Console always; Telegram and/or Discord when their env credentials are set."""
    channels: list[INotifier] = [ConsoleNotifier()]

    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if token and chat_id:
        channels.append(TelegramNotifier(token, chat_id))
        logger.info("telegram_notifier_enabled")
    elif token or chat_id:
        logger.warning(
            "telegram_notifier_incomplete",
            extra={
                "reason": "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must both be set",
            },
        )

    webhook = discord_webhook_for(settings)
    if webhook:
        channels.append(DiscordNotifier(webhook))
        logger.info(
            "discord_notifier_enabled",
            extra={"environment": settings.environment.value},
        )
    else:
        logger.info(
            "discord_notifier_disabled",
            extra={
                "environment": settings.environment.value,
                "reason": _discord_missing_reason(settings.environment),
            },
        )

    if len(channels) == 1:
        logger.info(
            "remote_notifiers_disabled",
            extra={
                "reason": (
                    "set the Discord webhook for this ENV "
                    "(DISCORD_WEBHOOK_URL / DISCORD_DEMO_WEBHOOK_URL / "
                    "DISCORD_LIVE_WEBHOOK_URL) and/or TELEGRAM_BOT_TOKEN+TELEGRAM_CHAT_ID"
                ),
            },
        )

    return CompositeNotifier(channels)


def _discord_missing_reason(environment: Environment) -> str:
    if environment == Environment.DEMO:
        return "DISCORD_DEMO_WEBHOOK_URL not set"
    if environment == Environment.LIVE:
        return "DISCORD_LIVE_WEBHOOK_URL not set"
    return "DISCORD_WEBHOOK_URL not set"
