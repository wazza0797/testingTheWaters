from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from trading_platform.domain.models.instrument_rules import InstrumentRules


def round_to_step(value: Decimal, step: Decimal, *, rounding: str = ROUND_DOWN) -> Decimal:
    """Round `value` to the nearest multiple of `step`.

    Defaults to rounding *down*: the conservative choice for both price and
    quantity, since it never produces a size/price the exchange would reject
    as insufficiently rounded, and never overstates order quantity (which
    would spend more than intended).
    """
    if step <= 0:
        raise ValueError(f"step must be positive, got {step}")
    quotient = (value / step).to_integral_value(rounding=rounding)
    return quotient * step


def round_price(price: Decimal, rules: InstrumentRules, *, rounding: str = ROUND_DOWN) -> Decimal:
    """Round a price to the instrument's tick size."""
    return round_to_step(price, rules.tick_size, rounding=rounding)


def round_qty(quantity: Decimal, rules: InstrumentRules, *, rounding: str = ROUND_DOWN) -> Decimal:
    """Round a quantity to the instrument's step size."""
    return round_to_step(quantity, rules.step_size, rounding=rounding)


def meets_min_qty(quantity: Decimal, rules: InstrumentRules) -> bool:
    return quantity >= rules.min_qty


def meets_min_notional(quantity: Decimal, price: Decimal, rules: InstrumentRules) -> bool:
    return quantity * price >= rules.min_notional
