from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from trading_platform.domain.models.fill import Fill
from trading_platform.domain.models.order import OrderSide


@dataclass(frozen=True, slots=True)
class RoundTrip:
    """One closed (or partially closed) long cycle reconstructed from fills.

    Long-only semantics (matching `PassThroughRiskEngine`): a position is
    opened by one or more `BUY` fills, then reduced/closed by `SELL` fills.
    Each `SELL` that reduces an open position emits a `RoundTrip` with PnL
    net of fees (entry fees amortized into `entry_price`; exit fee subtracted
    from proceeds).

    `is_partial` is `True` when quantity remains open after this exit.
    """

    symbol: str
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    entry_time: datetime
    exit_time: datetime
    pnl: Decimal
    fees: Decimal
    is_partial: bool

    @property
    def is_winner(self) -> bool:
        return self.pnl > 0


@dataclass
class _OpenLot:
    quantity: Decimal
    avg_entry_price: Decimal  # fee-amortized unit cost
    entry_time: datetime
    entry_fees: Decimal


def reconstruct_round_trips(fills: Sequence[Fill]) -> tuple[RoundTrip, ...]:
    """Walk fills in order and emit one `RoundTrip` per reducing `SELL`.

    Assumes long-only fills (M4/M5 scope). A `SELL` with no open position is
    ignored (defensive — the ledger would have rejected it upstream). Multiple
    symbols are tracked independently.
    """
    open_lots: dict[str, _OpenLot] = {}
    trips: list[RoundTrip] = []

    for fill in fills:
        if fill.side == OrderSide.BUY:
            _apply_buy(open_lots, fill)
        else:
            trip = _apply_sell(open_lots, fill)
            if trip is not None:
                trips.append(trip)

    return tuple(trips)


def _apply_buy(open_lots: dict[str, _OpenLot], fill: Fill) -> None:
    existing = open_lots.get(fill.symbol)
    fill_cost = fill.filled_qty * fill.fill_price + fill.fee
    if existing is None or existing.quantity <= 0:
        open_lots[fill.symbol] = _OpenLot(
            quantity=fill.filled_qty,
            avg_entry_price=fill_cost / fill.filled_qty,
            entry_time=fill.timestamp,
            entry_fees=fill.fee,
        )
        return

    new_qty = existing.quantity + fill.filled_qty
    prior_cost = existing.quantity * existing.avg_entry_price
    open_lots[fill.symbol] = _OpenLot(
        quantity=new_qty,
        avg_entry_price=(prior_cost + fill_cost) / new_qty,
        entry_time=existing.entry_time,
        entry_fees=existing.entry_fees + fill.fee,
    )


def _apply_sell(open_lots: dict[str, _OpenLot], fill: Fill) -> RoundTrip | None:
    existing = open_lots.get(fill.symbol)
    if existing is None or existing.quantity <= 0:
        return None

    sold_qty = min(fill.filled_qty, existing.quantity)
    prior_qty = existing.quantity
    entry_fee_share = (
        existing.entry_fees * (sold_qty / prior_qty) if prior_qty > 0 else Decimal("0")
    )
    exit_fees = fill.fee * (sold_qty / fill.filled_qty) if fill.filled_qty > 0 else Decimal("0")
    entry_cost = sold_qty * existing.avg_entry_price
    proceeds = sold_qty * fill.fill_price - exit_fees
    pnl = proceeds - entry_cost

    remaining = prior_qty - sold_qty
    is_partial = remaining > 0
    if is_partial:
        open_lots[fill.symbol] = _OpenLot(
            quantity=remaining,
            avg_entry_price=existing.avg_entry_price,
            entry_time=existing.entry_time,
            entry_fees=existing.entry_fees - entry_fee_share,
        )
    else:
        open_lots.pop(fill.symbol, None)

    return RoundTrip(
        symbol=fill.symbol,
        quantity=sold_qty,
        entry_price=existing.avg_entry_price,
        exit_price=fill.fill_price,
        entry_time=existing.entry_time,
        exit_time=fill.timestamp,
        pnl=pnl,
        fees=entry_fee_share + exit_fees,
        is_partial=is_partial,
    )
