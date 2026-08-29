"""Notification channels and event-driven NotificationHandler (Milestone 7)."""

from trading_platform.notifications.composite import CompositeNotifier
from trading_platform.notifications.console import ConsoleNotifier
from trading_platform.notifications.factory import build_notifier
from trading_platform.notifications.handler import NotificationHandler, format_event
from trading_platform.notifications.telegram import TelegramNotifier

__all__ = [
    "CompositeNotifier",
    "ConsoleNotifier",
    "NotificationHandler",
    "TelegramNotifier",
    "build_notifier",
    "format_event",
]
