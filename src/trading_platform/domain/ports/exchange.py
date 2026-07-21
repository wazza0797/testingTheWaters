from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol

from trading_platform.domain.models.bar import Bar
from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.domain.models.order import Order


class IExchangeAdapter(Protocol):
    """Isolates all exchange-specific (ccxt/Binance/...) code behind one port.

    Application code (market data ingest, execution, backtest) depends only on
    this Protocol — never on ccxt or a specific exchange module. Concrete
    implementations live under `exchanges/<name>/` (e.g. `exchanges/binance/`)
    and are the *only* place exchange-specific fields/quirks may appear.
    """

    @property
    def exchange_name(self) -> str: ...

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[Bar]:
        """Fetch historical OHLCV bars, ascending by timestamp."""
        ...

    def fetch_instrument_rules(self, symbol: str) -> InstrumentRules:
        """Fetch tick/step size, min qty/notional, and maker/taker fees for a symbol."""
        ...

    def place_order(self, order: Order) -> str:
        """Submit an order to the exchange. Returns the exchange-assigned order id."""
        ...

    def cancel_order(self, order_id: str, symbol: str) -> None: ...

    def get_balance(self, asset: str) -> Decimal: ...
