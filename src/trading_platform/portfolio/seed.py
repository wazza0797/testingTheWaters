from __future__ import annotations

from decimal import Decimal

from trading_platform.domain.models.position import Position
from trading_platform.domain.ports.exchange import IExchangeAdapter
from trading_platform.domain.symbols import split_symbol
from trading_platform.portfolio.book import PortfolioBook


def seed_book_from_exchange(
    adapter: IExchangeAdapter,
    symbol: str,
    *,
    timeframe: str,
) -> PortfolioBook:
    """Build a `PortfolioBook` from venue free balances (demo/live source of truth).

    Quote free balance → cash. Base free balance → open position; average entry
    uses the latest closed mark when available (approximate until fills update it).
    """
    base, quote = split_symbol(symbol)
    cash = adapter.get_balance(quote)
    base_qty = adapter.get_balance(base)
    positions: dict[str, Position] = {}
    if base_qty > 0:
        mark = _latest_mark(adapter, symbol, timeframe)
        positions[symbol] = Position(
            symbol=symbol,
            quantity=base_qty,
            average_entry_price=mark,
            realized_pnl=Decimal("0"),
        )
    return PortfolioBook.from_snapshot(cash, positions)


def _latest_mark(adapter: IExchangeAdapter, symbol: str, timeframe: str) -> Decimal:
    bars = adapter.fetch_ohlcv(symbol, timeframe, limit=1)
    if not bars:
        return Decimal("0")
    return bars[-1].close
