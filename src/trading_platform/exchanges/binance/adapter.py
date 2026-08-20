from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

import ccxt

from trading_platform.domain.errors import ExchangeAdapterError
from trading_platform.domain.models.bar import Bar
from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.domain.models.order import Order
from trading_platform.exchanges.binance.mapper import (
    EXCHANGE_NAME,
    map_instrument_rules,
    map_ohlcv_rows,
)
from trading_platform.utils.retry import retry_with_backoff

logger = logging.getLogger(__name__)

_MAX_OHLCV_LIMIT = 1000  # Binance API cap per request


class _CcxtExchange(Protocol):
    """The subset of the ccxt exchange interface this adapter depends on.

    Declared explicitly (rather than typing against `ccxt.binance` directly)
    so unit tests can pass a plain fake object instead of a real ccxt
    instance — no network, no ccxt internals in tests.
    """

    markets: dict[str, Any]

    def load_markets(self) -> dict[str, Any]: ...

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = ...,
        since: int | None = ...,
        limit: int | None = ...,
    ) -> list[list[Any]]: ...


class BinanceAdapter:
    """`IExchangeAdapter` implementation backed by `ccxt.binance`.

    This is the **only** module allowed to import `ccxt` or reference
    Binance-specific fields — see `docs/coding-standards.md`. Order
    placement/balance methods raise `NotImplementedError` until Milestone 8
    (live trading is gated); Milestone 1 only needs historical data + rules.
    """

    def __init__(self, exchange: _CcxtExchange | None = None) -> None:
        self._exchange: _CcxtExchange = exchange or ccxt.binance({"enableRateLimit": True})
        self._markets_loaded = False

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

    def place_order(self, order: Order) -> str:
        raise NotImplementedError("Live order placement is gated until Milestone 8.")

    def cancel_order(self, order_id: str, symbol: str) -> None:
        raise NotImplementedError("Live order placement is gated until Milestone 8.")

    def get_balance(self, asset: str) -> Decimal:
        raise NotImplementedError("Live balance queries are gated until Milestone 8.")
