from __future__ import annotations

import logging

from trading_platform.config.settings import Settings
from trading_platform.domain.ports.notification import INotifier
from trading_platform.notifications.composite import CompositeNotifier
from trading_platform.notifications.console import ConsoleNotifier
from trading_platform.notifications.telegram import TelegramNotifier

logger = logging.getLogger(__name__)


def build_notifier(settings: Settings) -> INotifier:
    """Console always; Telegram when both env credentials are present."""
    channels: list[INotifier] = [ConsoleNotifier()]
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if token and chat_id:
        channels.append(TelegramNotifier(token, chat_id))
        logger.info("telegram_notifier_enabled")
    else:
        logger.warning(
            "telegram_notifier_disabled",
            extra={
                "reason": "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must both be set",
            },
        )
    return CompositeNotifier(channels)
