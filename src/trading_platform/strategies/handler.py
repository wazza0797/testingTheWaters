from __future__ import annotations

import logging
from dataclasses import replace

from trading_platform.domain.errors import StrategyError
from trading_platform.domain.events.base import Event
from trading_platform.domain.events.market import BarClosed
from trading_platform.domain.events.strategy import SignalGenerated
from trading_platform.domain.ports.event_bus import IEventBus
from trading_platform.domain.ports.strategy import IStrategy, StrategyContext

logger = logging.getLogger(__name__)


class StrategyHandler:
    """Adapts one `IStrategy` instance to the event bus: subscribes to
    `BarClosed` for its configured symbol/timeframe only, and publishes a
    `SignalGenerated` for every `Signal` the strategy returns.

    One `StrategyHandler` wraps exactly one strategy instance bound to one
    symbol/timeframe/`name`. Running multiple strategies — or the same
    strategy class twice with different params, or on multiple symbols —
    means constructing multiple `StrategyHandler`s, each subscribed
    independently wherever `container.py` wires them; nothing in this class
    needs to change.

    This is also the single choke point every strategy's output passes
    through, regardless of which strategy produced it, which makes it the
    natural place to enforce two invariants no individual strategy can be
    trusted to get right on its own:

    - **Identity**: `name` (typically `strategies.loader.describe_strategy`,
      e.g. `"SmaCrossoverStrategy[BTC/USDT](fast_period=5,slow_period=20)"`)
      overwrites whatever the strategy itself set on `Signal.strategy_name`
      before publishing. This is what lets two instances of the *same*
      strategy class with different params (or on different symbols) be
      told apart in every metric/log/signal downstream — without relying on
      every strategy author to plumb a correct, unique name through by hand.
    - **Symbol integrity**: every returned `Signal.symbol` must match the
      `Bar.symbol` that triggered it. A mismatch is a strategy bug (e.g. a
      copy-paste error hardcoding the wrong symbol) that must never reach
      Risk/Execution silently.

    This is a critical-path handler (strategy -> risk -> execution):
    exceptions raised by the wrapped strategy — or by the invariant checks
    above — propagate to the caller (the event bus / trading loop), which
    decides whether to halt, per `docs/architecture.md`. Not yet wired into
    `container.py` — there is no `TradingLoop`/`BacktestEngine` to drive
    `BarClosed` for a real trading mode until Milestone 4.
    """

    def __init__(
        self,
        strategy: IStrategy,
        context: StrategyContext,
        event_bus: IEventBus,
        symbol: str,
        timeframe: str,
        name: str,
    ) -> None:
        self._strategy = strategy
        self._context = context
        self._event_bus = event_bus
        self._symbol = symbol
        self._timeframe = timeframe
        self._name = name
        self._started = False

    def handle(self, event: Event) -> None:
        if not isinstance(event, BarClosed):
            return
        bar = event.bar
        if bar.symbol != self._symbol or bar.timeframe != self._timeframe:
            return

        if not self._started:
            self._strategy.on_start(self._context)
            self._started = True

        signals = self._strategy.on_bar(bar, self._context)
        for signal in signals:
            if signal.symbol != bar.symbol:
                raise StrategyError(
                    f"Strategy {self._name!r} returned a signal for "
                    f"{signal.symbol!r} while processing a {bar.symbol!r} bar "
                    "— refusing to publish a mismatched signal."
                )

        for signal in signals:
            identified_signal = replace(signal, strategy_name=self._name)
            logger.debug(
                "signal_generated",
                extra={
                    "strategy": identified_signal.strategy_name,
                    "symbol": identified_signal.symbol,
                    "signal_type": identified_signal.signal_type.value,
                    "correlation_id": event.correlation_id,
                },
            )
            # Same correlation_id as the triggering bar: one ID traces the
            # whole strategy -> risk -> execution chain for this signal.
            self._event_bus.publish(
                SignalGenerated(signal=identified_signal, correlation_id=event.correlation_id)
            )

    def stop(self) -> None:
        """Invoke the strategy's `on_stop` hook, if it was ever started.

        Not tied to any event — no shutdown/lifecycle event exists yet (that
        lands alongside a real `TradingLoop`). Callers invoke this directly
        when tearing down.
        """
        if self._started:
            self._strategy.on_stop(self._context)
            self._started = False
