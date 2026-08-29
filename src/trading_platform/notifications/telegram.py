from __future__ import annotations

import logging
from collections.abc import Callable

import httpx

logger = logging.getLogger(__name__)

# Telegram Bot API: https://core.telegram.org/bots/api#sendmessage
_DEFAULT_TIMEOUT_SEC = 10.0


class TelegramNotifier:
    """Sends messages via Telegram Bot API `sendMessage`.

    Construct only when both bot token and chat id are configured. HTTP is
    injected for tests (`http_post`); production uses `httpx.post`.
    """

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        http_post: Callable[..., httpx.Response] | None = None,
        timeout_sec: float = _DEFAULT_TIMEOUT_SEC,
    ) -> None:
        if not bot_token.strip():
            raise ValueError("Telegram bot_token must be non-empty")
        if not chat_id.strip():
            raise ValueError("Telegram chat_id must be non-empty")
        self._bot_token = bot_token.strip()
        self._chat_id = chat_id.strip()
        self._http_post = http_post or httpx.post
        self._timeout_sec = timeout_sec

    @property
    def api_url(self) -> str:
        return f"https://api.telegram.org/bot{self._bot_token}/sendMessage"

    def notify(self, message: str, level: str = "info") -> None:
        prefix = level.upper()
        text = f"[{prefix}] {message}"
        response = self._http_post(
            self.api_url,
            json={"chat_id": self._chat_id, "text": text},
            timeout=self._timeout_sec,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Telegram HTTP {response.status_code}: {response.text[:200]}")
        payload = response.json()
        if not payload.get("ok", False):
            description = payload.get("description", "unknown Telegram error")
            raise RuntimeError(f"Telegram sendMessage failed: {description}")
        logger.debug("telegram_message_sent", extra={"level": level})
