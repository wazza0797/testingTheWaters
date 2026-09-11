"""Post research / validation run summaries to the Discord *demo* webhook.

Uses `DISCORD_DEMO_WEBHOOK_URL` explicitly (not `build_notifier()` / `ENV`),
so research scripts can notify the demo channel regardless of local `ENV`.
"""

from __future__ import annotations

import logging

from trading_platform.config.settings import Settings
from trading_platform.notifications.discord import DiscordNotifier

logger = logging.getLogger(__name__)


def notify_demo_research(
    summary: str,
    *,
    enabled: bool = True,
    settings: Settings | None = None,
    notifier: DiscordNotifier | None = None,
) -> bool:
    """Send `summary` to the Discord demo webhook.

    Returns True if a message was sent. When `enabled` is False, the webhook
    is unset, or posting fails, returns False without raising (research runs
    should not fail because Discord is down).
    """
    if not enabled:
        return False

    text = summary.strip()
    if not text:
        return False

    try:
        if notifier is None:
            cfg = settings if settings is not None else Settings()
            webhook = (cfg.discord_demo_webhook_url or "").strip()
            if not webhook:
                logger.info(
                    "discord_research_notify_skipped",
                    extra={"reason": "DISCORD_DEMO_WEBHOOK_URL not set"},
                )
                return False
            notifier = DiscordNotifier(webhook)
        notifier.notify(text, level="info")
    except Exception as exc:
        logger.warning(
            "discord_research_notify_failed",
            extra={"error": f"{type(exc).__name__}: {exc}"},
        )
        print(f"WARNING: Discord demo notify failed: {type(exc).__name__}: {exc}", flush=True)
        return False

    logger.info("discord_research_notify_sent")
    return True
