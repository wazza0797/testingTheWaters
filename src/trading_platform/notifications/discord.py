from __future__ import annotations

import logging
from collections.abc import Callable

import httpx

logger = logging.getLogger(__name__)

# Discord incoming webhooks: https://discord.com/developers/docs/resources/webhook
_DEFAULT_TIMEOUT_SEC = 10.0
_MAX_CONTENT_LEN = 2000


class DiscordNotifier:
    """Sends messages via a Discord incoming webhook URL.

    Construct only when `DISCORD_WEBHOOK_URL` is configured. HTTP is injected
    for tests (`http_post`); production uses `httpx.post`.
    """

    def __init__(
        self,
        webhook_url: str,
        *,
        http_post: Callable[..., httpx.Response] | None = None,
        timeout_sec: float = _DEFAULT_TIMEOUT_SEC,
    ) -> None:
        url = webhook_url.strip()
        if not url:
            raise ValueError("Discord webhook_url must be non-empty")
        self._webhook_url = url
        self._http_post = http_post or httpx.post
        self._timeout_sec = timeout_sec

    def notify(self, message: str, level: str = "info") -> None:
        prefix = level.upper()
        content = f"[{prefix}] {message}"
        if len(content) > _MAX_CONTENT_LEN:
            content = content[: _MAX_CONTENT_LEN - 1] + "…"
        response = self._http_post(
            self._webhook_url,
            json={"content": content},
            timeout=self._timeout_sec,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Discord HTTP {response.status_code}: {response.text[:200]}")
        logger.debug("discord_message_sent", extra={"level": level})
