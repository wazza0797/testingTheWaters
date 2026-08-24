from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Protocol

from trading_platform.domain.models.position import Position


class IPositionProvider(Protocol):
    """Read-only view over current positions, injected into `StrategyContext`.

    Backed by `NullPositionProvider` (always flat) for strategies run outside
    a backtest. Milestone 4's `backtesting.ledger.BacktestLedger` is the first
    real implementation (in-memory only, backtest-run-scoped — see its
    docstring for why a full event-driven, persisted `PortfolioHandler` is
    deliberately not being built yet). Strategies depend on this port either
    way, so swapping which implementation is injected never requires a
    strategy-facing change.
    """

    def position_for(self, symbol: str) -> Position | None: ...


class IPortfolioView(IPositionProvider, Protocol):
    """What `risk/engine.py::PassThroughRiskEngine` needs to size a `Signal`
    into an `Order`: current positions (inherited from `IPositionProvider`),
    total account equity, and raw available cash. Structurally,
    `domain/models/portfolio.py`'s `Portfolio` already has exactly this
    shape; `backtesting.ledger.BacktestLedger` is the first mutable, stateful
    implementation (M6's real `PortfolioHandler` will be the second).

    `cash` is distinct from `equity`: sizing a `BUY` against equity (cash +
    mark-to-market of any open positions) is the sizing *policy*, but
    affording the resulting order is strictly a function of raw `cash` — see
    `PassThroughRiskEngine`'s cash-sufficiency guard.
    """

    @property
    def cash(self) -> Decimal: ...

    def equity(self, mark_prices: Mapping[str, Decimal]) -> Decimal: ...
