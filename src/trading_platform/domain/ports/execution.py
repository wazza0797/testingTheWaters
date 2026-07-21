from __future__ import annotations

from typing import Protocol

from trading_platform.domain.models.fill import Fill
from trading_platform.domain.models.order import Order


class IBroker(Protocol):
    """A venue capable of accepting an Order and producing Fill(s).

    Implementations: `SimBroker` (backtest), `PaperBroker` (paper), `LiveBroker`
    (M8, gated). All three share the `FillSimulator` pipeline where applicable
    so fill behaviour is consistent across modes.
    """

    def submit_order(self, order: Order) -> list[Fill]: ...


class IExecutionEngine(Protocol):
    """Orchestrates order lifecycle against an `IBroker`. Wired into `ExecutionHandler`
    which subscribes to `OrderApproved` and publishes `FillReceived`/`OrderRejected`.
    """

    def execute(self, order: Order) -> list[Fill]: ...
