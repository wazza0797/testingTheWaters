from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from trading_platform.domain.errors import PortfolioError
from trading_platform.domain.models.fill import Fill
from trading_platform.domain.models.order import OrderSide
from trading_platform.domain.models.portfolio import Portfolio
from trading_platform.domain.models.position import Position


class BacktestLedger:
    """In-memory cash/position bookkeeping for a single backtest run.

    Deliberately **not** a `PortfolioHandler`: it is owned and driven directly
    by the backtest engine (via `apply_fill`), has no event-bus subscription,
    and has no disk persistence — a backtest run is a single, disposable
    process, so none of that machinery earns its complexity yet. Milestone 6
    builds the real, event-driven, persisted `PortfolioHandler` for paper
    trading; this class exists solely to give the M4 pass-through risk engine
    something to size against (`IPortfolioView` — `equity()`/`position_for`)
    and to give the backtest engine an equity curve and fill/fee history to
    assemble into a `BacktestResult`.

    Long-only (BTC/USDT is spot): a `BUY` fill only ever opens or adds to a
    position; a `SELL` fill only ever reduces or fully closes one.
    `apply_fill` raises `PortfolioError` if a `SELL` fill would sell more than
    is currently held, or arrives with no position open at all — both
    indicate a bug upstream (risk sizing or the fill simulator), not a normal
    runtime condition this ledger should absorb silently.
    """

    def __init__(self, starting_cash: Decimal) -> None:
        self._portfolio = Portfolio(cash=starting_cash, positions={})
        self._fills: list[Fill] = []

    def position_for(self, symbol: str) -> Position | None:
        return self._portfolio.position_for(symbol)

    def equity(self, mark_prices: Mapping[str, Decimal]) -> Decimal:
        return self._portfolio.equity(mark_prices)

    @property
    def cash(self) -> Decimal:
        return self._portfolio.cash

    @property
    def timestamp(self) -> datetime | None:
        """Timestamp of the most recently applied fill, or `None` before any fill."""
        return self._portfolio.timestamp

    @property
    def fills(self) -> tuple[Fill, ...]:
        return tuple(self._fills)

    @property
    def total_fees_paid(self) -> Decimal:
        return sum((fill.fee for fill in self._fills), Decimal("0"))

    def apply_fill(self, fill: Fill) -> None:
        """Update cash and the affected symbol's position from one `Fill`.

        Safe to call once per (possibly partial) fill — a large order filled
        across several bars by the `FillSimulator`'s partial-fill model is
        just several calls, each updating the running average entry price
        (on `BUY`) or realized P&L (on `SELL`) incrementally.
        """
        self._fills.append(fill)
        position = self._portfolio.positions.get(fill.symbol)
        if fill.side == OrderSide.BUY:
            self._apply_buy_fill(fill, position)
        else:
            self._apply_sell_fill(fill, position)

    def _apply_buy_fill(self, fill: Fill, position: Position | None) -> None:
        cost = fill.filled_qty * fill.fill_price + fill.fee
        new_cash = self._portfolio.cash - cost

        if position is None or position.is_flat:
            new_position = Position(
                symbol=fill.symbol,
                quantity=fill.filled_qty,
                average_entry_price=fill.fill_price,
            )
        else:
            new_quantity = position.quantity + fill.filled_qty
            new_average_price = (
                position.quantity * position.average_entry_price + fill.filled_qty * fill.fill_price
            ) / new_quantity
            new_position = Position(
                symbol=fill.symbol,
                quantity=new_quantity,
                average_entry_price=new_average_price,
                realized_pnl=position.realized_pnl,
            )

        self._set_position(fill.symbol, new_position, new_cash, fill.timestamp)

    def _apply_sell_fill(self, fill: Fill, position: Position | None) -> None:
        if position is None or position.is_flat:
            raise PortfolioError(
                f"received a SELL fill for {fill.symbol!r} with no open position — "
                "risk/order-sizing should never have allowed this order"
            )
        if fill.filled_qty > position.quantity:
            raise PortfolioError(
                f"SELL fill quantity {fill.filled_qty} for {fill.symbol!r} exceeds held "
                f"position quantity {position.quantity}"
            )

        proceeds = fill.filled_qty * fill.fill_price - fill.fee
        new_cash = self._portfolio.cash + proceeds
        realized_pnl_delta = (fill.fill_price - position.average_entry_price) * fill.filled_qty
        new_quantity = position.quantity - fill.filled_qty

        if new_quantity == 0:
            self._remove_position(fill.symbol, new_cash, fill.timestamp)
        else:
            new_position = Position(
                symbol=fill.symbol,
                quantity=new_quantity,
                average_entry_price=position.average_entry_price,
                realized_pnl=position.realized_pnl + realized_pnl_delta,
            )
            self._set_position(fill.symbol, new_position, new_cash, fill.timestamp)

    def _set_position(
        self, symbol: str, position: Position, cash: Decimal, timestamp: datetime
    ) -> None:
        positions = dict(self._portfolio.positions)
        positions[symbol] = position
        self._portfolio = replace(
            self._portfolio, cash=cash, positions=positions, timestamp=timestamp
        )

    def _remove_position(self, symbol: str, cash: Decimal, timestamp: datetime) -> None:
        positions = dict(self._portfolio.positions)
        positions.pop(symbol, None)
        self._portfolio = replace(
            self._portfolio, cash=cash, positions=positions, timestamp=timestamp
        )
