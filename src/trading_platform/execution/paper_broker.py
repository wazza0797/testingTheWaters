from __future__ import annotations

from collections.abc import Mapping

from trading_platform.backtesting.fill_simulator import FillSimulator
from trading_platform.backtesting.order_queue import OrderQueue
from trading_platform.domain.models.bar import Bar
from trading_platform.domain.models.fill import Fill
from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.domain.models.order import Order


class PaperBroker:
    """Paper-mode `IBroker`: same latency queue + `FillSimulator` as `SimBroker`.

    `submit_order` enqueues only; fills are produced in `process_bar` when the
    next closed candle arrives — mirroring backtest look-ahead control so
    paper fills stay realistic (spread, fees, partials, latency).
    """

    def __init__(
        self,
        fill_simulator: FillSimulator,
        order_queue: OrderQueue,
        instrument_rules: Mapping[str, InstrumentRules],
    ) -> None:
        self._fill_simulator = fill_simulator
        self._order_queue = order_queue
        self._instrument_rules = instrument_rules

    def submit_order(self, order: Order) -> list[Fill]:
        self._order_queue.enqueue(order)
        return []

    def has_pending_order(self, symbol: str) -> bool:
        return self._order_queue.has_pending_order(symbol)

    def process_bar(self, bar: Bar) -> list[tuple[Order, Fill]]:
        self._fill_simulator.observe_bar(bar)

        fills: list[tuple[Order, Fill]] = []
        for queued in self._order_queue.advance():
            order = queued.order
            if order.symbol != bar.symbol:
                continue

            rules = self._instrument_rules.get(order.symbol)
            if rules is None:
                continue

            fill = self._fill_simulator.simulate_fill(order, queued.remaining_qty, bar, rules)
            if fill is None:
                continue

            self._order_queue.record_fill(order.order_id, fill.filled_qty)
            fills.append((order, fill))

        return fills
