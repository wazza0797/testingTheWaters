from __future__ import annotations

from decimal import Decimal

from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.execution.precision import round_qty


class EquityFractionSizer:
    """Sizes a `BUY` order as a fixed fraction of current equity.

    There is no dedicated position-sizing module yet (`ISizer` is unscheduled
    future work — see the Milestone 10+ table in the project roadmap), and a
    `Signal` deliberately carries no quantity. This is the simplest sizing
    policy that unblocks the backtest engine: `quantity = (equity * fraction)
    / price`, rounded down to the instrument's `step_size` (never rounds up —
    see `execution/precision.py::round_to_step` — so a sized order never
    costs more than `fraction * equity`).

    `fraction=1.0` means "go all-in" on every entry signal; `< 1.0` reserves
    cash. Since positions are long-only and one `BUY` is rejected outright
    while already in a position (see `PassThroughRiskEngine`), there is never
    more than one open position sized against equity at a time.
    """

    def __init__(self, fraction: float) -> None:
        if not (0.0 < fraction <= 1.0):
            raise ValueError(f"fraction must be in (0.0, 1.0], got {fraction}")
        self._fraction = Decimal(str(fraction))

    def size(self, equity: Decimal, price: Decimal, rules: InstrumentRules) -> Decimal:
        if equity <= 0 or price <= 0:
            return Decimal("0")
        raw_quantity = (equity * self._fraction) / price
        return round_qty(raw_quantity, rules)
