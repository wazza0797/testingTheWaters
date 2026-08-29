from __future__ import annotations

import logging
from collections.abc import Sequence

from trading_platform.domain.ports.notification import INotifier

logger = logging.getLogger(__name__)


class CompositeNotifier:
    """Fan-out to multiple channels; one failure never skips the rest."""

    def __init__(self, notifiers: Sequence[INotifier]) -> None:
        self._notifiers = list(notifiers)

    @property
    def notifiers(self) -> tuple[INotifier, ...]:
        return tuple(self._notifiers)

    def notify(self, message: str, level: str = "info") -> None:
        for notifier in self._notifiers:
            try:
                notifier.notify(message, level)
            except Exception:
                logger.exception(
                    "notifier_failed",
                    extra={"notifier": type(notifier).__name__, "level": level},
                )
