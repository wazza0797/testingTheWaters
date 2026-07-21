from __future__ import annotations

from dataclasses import dataclass

from trading_platform.domain.events.base import Event


@dataclass(frozen=True, slots=True, kw_only=True)
class ErrorOccurred(Event):
    """Published by any handler on an unrecoverable error. Critical handlers halt the loop."""

    source: str
    error_type: str
    message: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Heartbeat(Event):
    """Published periodically by TradingLoop so notifications/health checks can detect liveness."""

    mode: str
    uptime_seconds: float
