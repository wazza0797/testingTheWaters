from __future__ import annotations

from collections.abc import Iterable

from trading_platform.application.trading_loop import TradingLoop
from trading_platform.backtesting.broker_sim import SimBroker
from trading_platform.backtesting.ledger import BacktestLedger
from trading_platform.backtesting.result import BacktestResult, EquityPoint
from trading_platform.domain.events.execution import FillReceived
from trading_platform.domain.models.bar import Bar
from trading_platform.domain.ports.event_bus import IEventBus


class BacktestEngine:
    """Replays historical bars through the shared event pipeline
    (`TradingLoop` -> `BarClosed` -> Strategy -> Risk -> Execution) and
    assembles a `BacktestResult`.

    The one thing backtest mode adds on top of the generic `TradingLoop`:
    before each bar's `BarClosed` triggers the strategy, drain `SimBroker`'s
    pending order queue against *that* bar's data (no look-ahead — an order
    submitted reacting to bar N can only ever fill starting at bar N+1, see
    `LatencyModel`), applying every resulting fill to the ledger and
    publishing `FillReceived` directly — `ExecutionHandler` only publishes
    fills it gets synchronously from `submit_order`, which `SimBroker` never
    does (see `SimBroker`'s docstring), so this is the only place backtest
    fills ever reach the event bus.
    """

    def __init__(
        self,
        event_bus: IEventBus,
        broker: SimBroker,
        ledger: BacktestLedger,
        symbol: str,
    ) -> None:
        self._event_bus = event_bus
        self._broker = broker
        self._ledger = ledger
        self._symbol = symbol
        self._trading_loop = TradingLoop(event_bus, mode="backtest")

    def run(self, bars: Iterable[Bar], timeframe: str) -> BacktestResult:
        starting_cash = self._ledger.cash
        equity_curve: list[EquityPoint] = []

        bars_processed = self._trading_loop.run(
            bars,
            before_bar=self._drain_pending_orders,
            after_bar=lambda bar: equity_curve.append(self._equity_point(bar)),
        )

        return BacktestResult(
            symbol=self._symbol,
            timeframe=timeframe,
            starting_cash=starting_cash,
            ending_cash=self._ledger.cash,
            bars_processed=bars_processed,
            fills=self._ledger.fills,
            total_fees_paid=self._ledger.total_fees_paid,
            equity_curve=tuple(equity_curve),
            final_position=self._ledger.position_for(self._symbol),
        )

    def _drain_pending_orders(self, bar: Bar) -> None:
        for order, fill in self._broker.process_bar(bar):
            self._ledger.apply_fill(fill)
            self._event_bus.publish(
                FillReceived(fill=fill, order=order, correlation_id=order.correlation_id)
            )

    def _equity_point(self, bar: Bar) -> EquityPoint:
        equity = self._ledger.equity({self._symbol: bar.close})
        return EquityPoint(timestamp=bar.timestamp, equity=equity)
