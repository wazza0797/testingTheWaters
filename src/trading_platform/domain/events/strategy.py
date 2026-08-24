from __future__ import annotations

from dataclasses import dataclass

from trading_platform.domain.events.base import Event
from trading_platform.domain.models.bar import Bar
from trading_platform.domain.models.signal import Signal


@dataclass(frozen=True, slots=True, kw_only=True)
class SignalGenerated(Event):
    """Published by StrategyHandler when a strategy emits a Signal for a closed bar.

    Carries the triggering `bar` alongside the `signal` because `Signal` itself
    has no price (a strategy's intent is deliberately quantity/price-free — see
    `domain/models/signal.py`). `RiskHandler` (Milestone 4) needs a reference
    price to size a `Signal` into an `Order`, and the bar that triggered the
    signal is exactly that price, already available at the point
    `StrategyHandler` publishes this event — no separate `BarClosed` cache
    needed downstream.
    """

    signal: Signal
    bar: Bar
