from __future__ import annotations

from collections.abc import Callable, Iterable

from trading_platform.domain.events.market import BarClosed
from trading_platform.domain.models.bar import Bar
from trading_platform.domain.ports.event_bus import IEventBus


class TradingLoop:
    """Drives time forward for one run: publish exactly one `BarClosed` per
    bar, in order. That is the *only* thing every mode (backtest replay,
    future live/paper polling) shares — everything mode-specific is a hook
    the caller supplies, not logic living here.

    `before_bar`/`after_bar` exist so `BacktestEngine` can drain its pending
    order queue against a bar's data *before* the strategy reacts to it (no
    look-ahead), and record an equity-curve point *after* — without this
    class needing to know anything about orders, fills, or a ledger. A
    future live/paper loop reuses this unchanged with a live bar source and
    no hooks (or different ones).
    """

    def __init__(self, event_bus: IEventBus, mode: str) -> None:
        self._event_bus = event_bus
        self._mode = mode

    def run(
        self,
        bars: Iterable[Bar],
        *,
        before_bar: Callable[[Bar], None] | None = None,
        after_bar: Callable[[Bar], None] | None = None,
    ) -> int:
        """Publish `BarClosed` for every bar in `bars`, in order. Returns the
        number of bars processed.
        """
        bar_count = 0
        for bar in bars:
            if before_bar is not None:
                before_bar(bar)
            self._event_bus.publish(BarClosed(bar=bar, mode=self._mode))
            if after_bar is not None:
                after_bar(bar)
            bar_count += 1
        return bar_count
