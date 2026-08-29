from __future__ import annotations

from typing import Protocol

from trading_platform.domain.models.fill import Fill
from trading_platform.domain.models.order import Order


class IBroker(Protocol):
    """A venue capable of accepting an Order and producing Fill(s).

    Implementations (selected by composition root from execution mode):

    - `SimBroker` — backtest, bar-driven `FillSimulator`
    - `PaperBroker` — local paper, same simulator as backtest
    - `DemoBroker` — exchange demo/practice/testnet via `IExchangeAdapter`
    - `LiveBroker` — mainnet via the same adapter port (later; double-gated)

    Strategy/risk/execution handlers depend only on this Protocol — never on a
    concrete broker or exchange SDK.
    """

    def submit_order(self, order: Order) -> list[Fill]: ...


class IExecutionEngine(Protocol):
    """Orchestrates order lifecycle against an `IBroker`. Wired into `ExecutionHandler`
    which subscribes to `OrderApproved` and publishes `FillReceived`/`OrderRejected`.
    """

    def execute(self, order: Order) -> list[Fill]: ...
