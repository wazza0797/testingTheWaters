from __future__ import annotations

from dataclasses import dataclass

from trading_platform.domain.events.base import Event
from trading_platform.domain.models.signal import Signal


@dataclass(frozen=True, slots=True, kw_only=True)
class SignalGenerated(Event):
    """Published by StrategyHandler when a strategy emits a Signal for a closed bar."""

    signal: Signal
