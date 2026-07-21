from __future__ import annotations

from typing import Protocol

from trading_platform.domain.models.bar import Bar
from trading_platform.domain.models.signal import Signal


class StrategyContext(Protocol):
    """Read-only view exposed to strategies: indicator helpers, current positions,
    and config params. Fleshed out in Milestone 3 alongside `indicators/` and
    `strategies/`; defined here so `IStrategy` has a stable signature from M0.
    """


class IStrategy(Protocol):
    """A pluggable strategy. Must be testable with synthetic bars and zero
    imports from `exchanges/`, `execution/`, or `ccxt` (see coding standards).
    """

    def on_start(self, ctx: StrategyContext) -> None: ...

    def on_bar(self, bar: Bar, ctx: StrategyContext) -> list[Signal]: ...

    def on_stop(self, ctx: StrategyContext) -> None: ...
