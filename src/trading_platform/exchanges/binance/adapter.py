from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

import ccxt

from trading_platform.domain.errors import ExchangeAdapterError
from trading_platform.domain.models.bar import Bar
from trading_platform.domain.models.exchange_order import ExchangeOrderStatus
from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.domain.models.order import Order, OrderType
from trading_platform.exchanges.binance.mapper import (
    EXCHANGE_NAME,
    map_ccxt_order,
    map_instrument_rules,
    map_ohlcv_rows,
)
from trading_platform.utils.retry import retry_with_backoff

logger = logging.getLogger(__name__)

_MAX_OHLCV_LIMIT = 1000  # Binance API cap per request


class _CcxtExchange(Protocol):
    """The subset of the ccxt exchange interface this adapter depends on."""

    markets: dict[str, Any]

    def load_markets(self) -> dict[str, Any]: ...

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = ...,
        since: int | None = ...,
        limit: int | None = ...,
    ) -> list[list[Any]]: ...

    def create_order(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: float | None = ...,
        params: dict[str, Any] = ...,
    ) -> dict[str, Any]: ...

    def cancel_order(
        self, id: str, symbol: str | None = ..., params: dict[str, Any] = ...
    ) -> dict[str, Any]: ...

    def fetch_order(
        self, id: str, symbol: str | None = ..., params: dict[str, Any] = ...
    ) -> dict[str, Any]: ...

    def fetch_balance(self, params: dict[str, Any] = ...) -> dict[str, Any]: ...

    def enable_demo_trading(self, enabled: bool) -> None: ...


class BinanceAdapter:
    """`IExchangeAdapter` implementation backed by `ccxt.binance`.

    This is the **only** module allowed to import `ccxt` or reference
    Binance-specific fields — see `docs/coding-standards.md`.

    Construct via `__init__` for public market data, or `for_demo` /
    (later) `for_live` so demo/mainnet URLs never leak into application code.
    """

    def __init__(self, exchange: _CcxtExchange | None = None) -> None:
        self._exchange: _CcxtExchange = exchange or ccxt.binance({"enableRateLimit": True})
        self._markets_loaded = False

    @classmethod
    def for_demo(
        cls,
        *,
        api_key: str | None,
        api_secret: str | None,
        exchange: _CcxtExchange | None = None,
    ) -> BinanceAdapter:
        """Build an adapter aimed at Binance Demo Trading (`enable_demo_trading`)."""
        if exchange is not None:
            return cls(exchange)
        if not api_key or not api_secret:
            raise ExchangeAdapterError(
                "ENV=demo requires BINANCE_DEMO_API_KEY and BINANCE_DEMO_API_SECRET "
                "(Demo Trading keys from demo.binance.com — not live BINANCE_API_* keys)."
            )
        client: Any = ccxt.binance(
            {
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": True,
                "options": {"defaultType": "spot"},
            }
        )
        client.enable_demo_trading(True)
        return cls(client)

    @property
    def exchange_name(self) -> str:
        return EXCHANGE_NAME

    def _ensure_markets_loaded(self) -> None:
        if not self._markets_loaded:
            self._exchange.load_markets()
            self._markets_loaded = True

    @retry_with_backoff(max_attempts=3, base_delay_seconds=1.0, exceptions=(ccxt.NetworkError,))
    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[Bar]:
        since_ms = int(since.timestamp() * 1000) if since is not None else None
        try:
            rows = self._exchange.fetch_ohlcv(
                symbol, timeframe=timeframe, since=since_ms, limit=limit or _MAX_OHLCV_LIMIT
            )
        except ccxt.BaseError as exc:
            raise ExchangeAdapterError(
                f"fetch_ohlcv failed for {symbol}@{timeframe}: {exc}"
            ) from exc
        return map_ohlcv_rows(symbol, timeframe, rows)

    @retry_with_backoff(max_attempts=3, base_delay_seconds=1.0, exceptions=(ccxt.NetworkError,))
    def fetch_instrument_rules(self, symbol: str) -> InstrumentRules:
        self._ensure_markets_loaded()
        try:
            market = self._exchange.markets[symbol]
        except KeyError as exc:
            raise ExchangeAdapterError(f"Unknown symbol on {EXCHANGE_NAME}: {symbol}") from exc
        return map_instrument_rules(symbol, market)

    @retry_with_backoff(max_attempts=3, base_delay_seconds=1.0, exceptions=(ccxt.NetworkError,))
    def place_order(self, order: Order) -> str:
        self._ensure_markets_loaded()
        price = float(order.price) if order.price is not None else None
        if order.order_type == OrderType.LIMIT and price is None:
            raise ExchangeAdapterError("Limit orders require a price")
        try:
            result = self._exchange.create_order(
                order.symbol,
                order.order_type.value,
                order.side.value,
                float(order.quantity),
                price,
                {"newClientOrderId": order.order_id},
            )
        except ccxt.BaseError as exc:
            raise ExchangeAdapterError(f"place_order failed for {order.symbol}: {exc}") from exc
        order_id = result.get("id")
        if not order_id:
            raise ExchangeAdapterError("place_order returned no exchange order id")
        return str(order_id)

    @retry_with_backoff(max_attempts=3, base_delay_seconds=1.0, exceptions=(ccxt.NetworkError,))
    def cancel_order(self, order_id: str, symbol: str) -> None:
        try:
            self._exchange.cancel_order(order_id, symbol)
        except ccxt.BaseError as exc:
            raise ExchangeAdapterError(
                f"cancel_order failed for {symbol} id={order_id}: {exc}"
            ) from exc

    @retry_with_backoff(max_attempts=3, base_delay_seconds=1.0, exceptions=(ccxt.NetworkError,))
    def get_balance(self, asset: str) -> Decimal:
        try:
            balance = self._exchange.fetch_balance()
        except ccxt.BaseError as exc:
            raise ExchangeAdapterError(f"get_balance failed for {asset}: {exc}") from exc
        free = balance.get("free") or {}
        if asset in free and free[asset] is not None:
            return Decimal(str(free[asset]))
        asset_entry = balance.get(asset)
        if isinstance(asset_entry, dict) and asset_entry.get("free") is not None:
            return Decimal(str(asset_entry["free"]))
        return Decimal("0")

    @retry_with_backoff(max_attempts=3, base_delay_seconds=1.0, exceptions=(ccxt.NetworkError,))
    def fetch_order(self, order_id: str, symbol: str) -> ExchangeOrderStatus:
        try:
            raw = self._exchange.fetch_order(order_id, symbol)
        except ccxt.BaseError as exc:
            raise ExchangeAdapterError(
                f"fetch_order failed for {symbol} id={order_id}: {exc}"
            ) from exc
        return map_ccxt_order(raw)
