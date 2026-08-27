from __future__ import annotations

from collections.abc import Mapping

from trading_platform.backtesting.fill_simulator import FillSimulator
from trading_platform.backtesting.order_queue import OrderQueue
from trading_platform.domain.models.bar import Bar
from trading_platform.domain.models.fill import Fill
from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.domain.models.order import Order


class SimBroker:
    """The backtest's `IBroker`: implements `submit_order` for
    `ExecutionHandler`, but the real work — actually producing `Fill`s —
    happens in `process_bar`, called once per bar by the backtest engine
    *before* that bar's `BarClosed` is published (so any orders activated by
    this bar are filled before the strategy reacts to it).

    `submit_order` always returns an empty list: latency means an order
    submitted after bar *N* closes can never fill against bar *N* itself
    (see `LatencyModel`'s docstring on why even `latency_bars=0` still
    defers to bar *N+1*) — every real fill comes from a later `process_bar`
    call, published by the engine as `FillReceived`.
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
        """Implements `IPendingOrderTracker` for `PassThroughRiskEngine` —
        delegates to the underlying `OrderQueue`, which is the actual source
        of truth for "still working, not yet resolved" orders.
        """
        return self._order_queue.has_pending_order(symbol)

    def process_bar(self, bar: Bar) -> list[tuple[Order, Fill]]:
        """Advance the order queue's latency and attempt a fill for every
        order now eligible, using `bar`'s OHLCV data. Returns `(order, fill)`
        pairs — the caller (`BacktestEngine`) needs the `Order` to publish
        `FillReceived`, which `Fill` alone (order_id string only) can't give it.

        Must be called exactly once per bar in the replay (one call =
        one latency tick for every pending order). Milestone 4 is
        single-symbol only (`config.trading.symbol`), so this is never
        called with interleaved bars from more than one symbol; the
        symbol check below is a defensive guard, not a multi-symbol
        scheduling mechanism — that's unscheduled future work.
        """
        self._fill_simulator.observe_bar(bar)

        fills: list[tuple[Order, Fill]] = []
        for queued in self._order_queue.advance():
            order = queued.order
            if order.symbol != bar.symbol:
                continue

            rules = self._instrument_rules.get(order.symbol)
            if rules is None:
                continue  # defensive — OrderValidator already checked this upstream

            fill = self._fill_simulator.simulate_fill(order, queued.remaining_qty, bar, rules)
            if fill is None:
                continue

            self._order_queue.record_fill(order.order_id, fill.filled_qty)
            fills.append((order, fill))

        return fills
