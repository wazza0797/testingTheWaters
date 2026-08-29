from __future__ import annotations

import logging

from trading_platform.config.settings import Settings
from trading_platform.domain.ports.notification import INotifier
from trading_platform.notifications.composite import CompositeNotifier
from trading_platform.notifications.console import ConsoleNotifier
from trading_platform.notifications.discord import DiscordNotifier
from trading_platform.notifications.telegram import TelegramNotifier

logger = logging.getLogger(__name__)


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

    webhook = settings.discord_webhook_url
    if webhook:
        channels.append(DiscordNotifier(webhook))
        logger.info("discord_notifier_enabled")

    if len(channels) == 1:
        logger.info(
            "remote_notifiers_disabled",
            extra={
                "reason": "set DISCORD_WEBHOOK_URL and/or TELEGRAM_BOT_TOKEN+TELEGRAM_CHAT_ID",
            },
        )

    return CompositeNotifier(channels)
