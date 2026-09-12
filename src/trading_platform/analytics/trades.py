from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from trading_platform.domain.models.fill import Fill
from trading_platform.domain.models.order import OrderSide


@dataclass(frozen=True, slots=True)
class RoundTrip:
    """One closed (or partially closed) directional cycle reconstructed from fills.

    Long **and** short round trips are both supported (CFD/FX venues allow
    `allows_short=True` — see `risk/engine.py`):

    - **Long**: opened by one or more `BUY` fills while flat, closed/reduced
      by `SELL` fill(s). PnL = `(exit_price - entry_price) * quantity - fees`.
    - **Short**: opened by one or more `SELL` fills while flat, closed/reduced
      (covered) by `BUY` fill(s). PnL = `(entry_price - exit_price) * quantity
      - fees`.

    `side` records which cycle this was (`BUY` = long, `SELL` = short).
    Entry fees are amortized into `entry_price` (raising cost for longs,
    lowering proceeds for shorts); the exit fee is subtracted from proceeds
    (long) or added to cost (short cover). `fees` is the total (entry-share +
    exit) for reporting only — already baked into `pnl` via `entry_price` /
    `exit_price` adjustments described above.

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
    side: OrderSide = OrderSide.BUY

    @property
    def is_long(self) -> bool:
        return self.side == OrderSide.BUY

    @property
    def is_short(self) -> bool:
        return self.side == OrderSide.SELL

    @property
    def is_winner(self) -> bool:
        return self.pnl > 0


@dataclass
class _OpenLot:
    """`side` is the side that *opened* this lot: `BUY` = long, `SELL` = short.

    `avg_entry_price` is fee-adjusted per unit in the direction that hurts
    the position — higher for a long (fee raises cost basis), lower for a
    short (fee reduces proceeds received) — via `_signed_fill_value`.
    """

    side: OrderSide
    quantity: Decimal  # always a positive magnitude, regardless of side
    avg_entry_price: Decimal
    entry_time: datetime
    entry_fees: Decimal


def _signed_fill_value(side: OrderSide, quantity: Decimal, price: Decimal, fee: Decimal) -> Decimal:
    """Cash value of a fill, fee-adjusted in the direction that costs the trader.

    A `BUY` costs `quantity * price + fee` (fee raises what you paid); a
    `SELL` nets `quantity * price - fee` (fee lowers what you received).
    Used both to open/extend a lot (dividing by quantity gives the
    fee-adjusted average entry price) and to close one (the raw value is
    compared against the entry value share to get PnL).
    """
    raw = quantity * price
    return raw + fee if side == OrderSide.BUY else raw - fee


def reconstruct_round_trips(fills: Sequence[Fill]) -> tuple[RoundTrip, ...]:
    """Walk fills in order and emit one `RoundTrip` per fill that reduces or
    closes the currently open lot for its symbol (long **or** short).

    A fill on the *same* side as the currently open lot (`BUY` while long,
    `SELL` while short) extends/pyramids that lot instead of closing it. A
    fill while flat opens a new lot (long for `BUY`, short for `SELL`) — no
    trip is emitted until that lot is later reduced. A fill on the opposite
    side of the open lot reduces or closes it, emitting a `RoundTrip`.

    Assumes fills never "flip" a position past flat in one fill (matches
    `PassThroughRiskEngine`, which always sizes closes to exactly the open
    quantity — see `risk/engine.py`); any excess beyond the open quantity is
    conservatively dropped rather than opening the opposite side, mirroring
    the pre-existing long-only behaviour for an unexpected over-sized fill.
    Multiple symbols are tracked independently.
    """
    open_lots: dict[str, _OpenLot] = {}
    trips: list[RoundTrip] = []

    for fill in fills:
        existing = open_lots.get(fill.symbol)
        if existing is None or existing.quantity <= 0:
            _open_or_extend_lot(open_lots, None, fill)
        elif existing.side == fill.side:
            _open_or_extend_lot(open_lots, existing, fill)
        else:
            trip = _reduce_lot(open_lots, existing, fill)
            if trip is not None:
                trips.append(trip)

    return tuple(trips)


def _open_or_extend_lot(
    open_lots: dict[str, _OpenLot], existing: _OpenLot | None, fill: Fill
) -> None:
    fill_value = _signed_fill_value(fill.side, fill.filled_qty, fill.fill_price, fill.fee)
    if existing is None:
        open_lots[fill.symbol] = _OpenLot(
            side=fill.side,
            quantity=fill.filled_qty,
            avg_entry_price=fill_value / fill.filled_qty,
            entry_time=fill.timestamp,
            entry_fees=fill.fee,
        )
        return

    new_qty = existing.quantity + fill.filled_qty
    prior_value = existing.quantity * existing.avg_entry_price
    open_lots[fill.symbol] = _OpenLot(
        side=existing.side,
        quantity=new_qty,
        avg_entry_price=(prior_value + fill_value) / new_qty,
        entry_time=existing.entry_time,
        entry_fees=existing.entry_fees + fill.fee,
    )


def _reduce_lot(open_lots: dict[str, _OpenLot], existing: _OpenLot, fill: Fill) -> RoundTrip | None:
    reduced_qty = min(fill.filled_qty, existing.quantity)
    if reduced_qty <= 0:
        return None

    prior_qty = existing.quantity
    entry_fee_share = (
        existing.entry_fees * (reduced_qty / prior_qty) if prior_qty > 0 else Decimal("0")
    )
    exit_fees = fill.fee * (reduced_qty / fill.filled_qty) if fill.filled_qty > 0 else Decimal("0")

    entry_value = reduced_qty * existing.avg_entry_price
    exit_value = _signed_fill_value(fill.side, reduced_qty, fill.fill_price, exit_fees)

    # Long lot (opened by BUY): pnl = proceeds (exit_value) - cost (entry_value).
    # Short lot (opened by SELL): pnl = proceeds received at entry (entry_value)
    # - cost to cover (exit_value) — the mirror image.
    pnl = exit_value - entry_value if existing.side == OrderSide.BUY else entry_value - exit_value

    remaining = prior_qty - reduced_qty
    is_partial = remaining > 0
    if is_partial:
        open_lots[fill.symbol] = _OpenLot(
            side=existing.side,
            quantity=remaining,
            avg_entry_price=existing.avg_entry_price,
            entry_time=existing.entry_time,
            entry_fees=existing.entry_fees - entry_fee_share,
        )
    else:
        open_lots.pop(fill.symbol, None)

    return RoundTrip(
        symbol=fill.symbol,
        side=existing.side,
        quantity=reduced_qty,
        entry_price=existing.avg_entry_price,
        exit_price=fill.fill_price,
        entry_time=existing.entry_time,
        exit_time=fill.timestamp,
        pnl=pnl,
        fees=entry_fee_share + exit_fees,
        is_partial=is_partial,
    )
