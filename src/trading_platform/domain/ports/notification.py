from __future__ import annotations

from typing import Protocol


class INotifier(Protocol):
    """A single notification channel (console, Telegram, ...). `NotificationHandler`
    fans out to a `CompositeNotifier` of these — never called directly by
    execution/risk/strategy modules.
    """

    def notify(self, message: str, level: str = "info") -> None: ...
