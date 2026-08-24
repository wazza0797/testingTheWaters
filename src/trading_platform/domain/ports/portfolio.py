from __future__ import annotations

from typing import Protocol

from trading_platform.domain.models.position import Position


class IPositionProvider(Protocol):
    """Read-only view over current positions, injected into `StrategyContext`.

    Backed by `NullPositionProvider` (always flat — no position tracking
    exists yet) until Milestone 5's `PortfolioHandler` tracks real positions
    from fills. Strategies depend on this port either way, so swapping the
    stub for the real implementation later requires no strategy changes.
    """

    def position_for(self, symbol: str) -> Position | None: ...
