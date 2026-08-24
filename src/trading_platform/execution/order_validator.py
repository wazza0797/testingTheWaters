from __future__ import annotations

from decimal import Decimal

from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.domain.models.order import Order
from trading_platform.execution.precision import meets_min_notional, meets_min_qty


def validate_order(
    order: Order, rules: InstrumentRules, market_reference_price: Decimal
) -> str | None:
    """Check an already-sized, already-rounded `Order` against exchange rules.

    Returns `None` if the order is acceptable, or a human-readable rejection
    reason string otherwise — never raises. Callers (`ExecutionHandler`)
    publish `OrderRejected(order, reason)` on a non-`None` result rather than
    silently dropping the order (Milestone 4 acceptance criterion).

    Rounding is *not* this function's job — `risk/sizing.py` already rounds
    quantity to `rules.step_size` before an `Order` is constructed (see
    `domain/models/order.py`'s docstring). This only checks the exchange's
    hard floors: `min_qty` and `min_notional`.

    `market_reference_price` is `order.price` for limit orders, but a market
    order has no price — the caller supplies the triggering bar's close (see
    `OrderApproved.bar`) as the best available estimate. The *actual* fill
    price (after spread) is determined later by the `FillSimulator`; this is
    only a pre-queue sanity check, not the final notional.
    """
    if order.quantity <= 0:
        return f"order quantity must be positive, got {order.quantity}"

    if not meets_min_qty(order.quantity, rules):
        return f"order quantity {order.quantity} is below {rules.symbol}'s min_qty {rules.min_qty}"

    reference_price = order.price if order.price is not None else market_reference_price
    if not meets_min_notional(order.quantity, reference_price, rules):
        notional = order.quantity * reference_price
        return (
            f"order notional {notional} ({order.quantity} @ {reference_price}) is below "
            f"{rules.symbol}'s min_notional {rules.min_notional}"
        )

    return None
