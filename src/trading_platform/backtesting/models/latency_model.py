from __future__ import annotations


class LatencyModel:
    """Validated wrapper around `backtest.latency_bars`: how many bars must
    pass after an order is submitted before it's eligible to fill.

    This is intentionally just a config value object — the actual per-order
    countdown/readiness tracking lives in `backtesting/order_queue.py::OrderQueue`,
    which uses `latency_bars` to seed each newly-enqueued order.

    Since a `SignalGenerated`/`OrderApproved` for bar *N* is only ever
    published *after* bar *N* has fully closed, there is no way for an order
    to fill using bar *N*'s own data without look-ahead bias — the earliest
    physically meaningful fill is bar *N+1*. `latency_bars=0` and
    `latency_bars=1` are therefore equivalent in practice (both fill at bar
    *N+1*); `latency_bars` only has visible effect at 2 or more.
    """

    def __init__(self, latency_bars: int) -> None:
        if latency_bars < 0:
            raise ValueError(f"latency_bars must be non-negative, got {latency_bars}")
        self.latency_bars = latency_bars
