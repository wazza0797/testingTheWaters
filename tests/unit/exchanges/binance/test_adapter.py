from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import ccxt
import pytest

from trading_platform.domain.errors import ExchangeAdapterError
from trading_platform.exchanges.binance.adapter import BinanceAdapter


class FakeCcxtExchange:
    """Duck-typed stand-in for `ccxt.binance` — no network, no real ccxt calls."""

    def __init__(
        self,
        markets: dict[str, Any] | None = None,
        ohlcv_rows: list[list[Any]] | None = None,
        ohlcv_error: Exception | None = None,
    ) -> None:
        self.markets = markets or {}
        self._ohlcv_rows = ohlcv_rows or []
        self._ohlcv_error = ohlcv_error
        self.load_markets_calls = 0
        self.fetch_ohlcv_calls: list[dict[str, Any]] = []

    def load_markets(self) -> dict[str, Any]:
        self.load_markets_calls += 1
        return self.markets

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        since: int | None = None,
        limit: int | None = None,
    ) -> list[list[Any]]:
        self.fetch_ohlcv_calls.append(
            {"symbol": symbol, "timeframe": timeframe, "since": since, "limit": limit}
        )
        if self._ohlcv_error is not None:
            raise self._ohlcv_error
        return self._ohlcv_rows


BTC_MARKET = {
    "precision": {"amount": 0.00001, "price": 0.01},
    "limits": {"amount": {"min": 0.00001}, "cost": {"min": 5.0}},
    "maker": 0.001,
    "taker": 0.001,
}


class TestExchangeName:
    def test_returns_binance(self) -> None:
        adapter = BinanceAdapter(exchange=FakeCcxtExchange())
        assert adapter.exchange_name == "binance"


class TestFetchOhlcv:
    def test_maps_rows_to_bars(self) -> None:
        rows = [[1704067200000, "100", "110", "90", "105", "1"]]
        fake = FakeCcxtExchange(ohlcv_rows=rows)
        adapter = BinanceAdapter(exchange=fake)

        bars = adapter.fetch_ohlcv("BTC/USDT", "1h")

        assert len(bars) == 1
        assert bars[0].close == Decimal("105")

    def test_passes_since_as_milliseconds(self) -> None:
        fake = FakeCcxtExchange(ohlcv_rows=[])
        adapter = BinanceAdapter(exchange=fake)
        since = datetime(2024, 1, 1, tzinfo=UTC)

        adapter.fetch_ohlcv("BTC/USDT", "1h", since=since)

        assert fake.fetch_ohlcv_calls[0]["since"] == 1704067200000

    def test_ccxt_base_error_is_wrapped_in_exchange_adapter_error(self) -> None:
        fake = FakeCcxtExchange(ohlcv_error=ccxt.ExchangeError("boom"))
        adapter = BinanceAdapter(exchange=fake)

        with pytest.raises(ExchangeAdapterError):
            adapter.fetch_ohlcv("BTC/USDT", "1h")


class TestFetchInstrumentRules:
    def test_loads_markets_lazily_and_only_once(self) -> None:
        fake = FakeCcxtExchange(markets={"BTC/USDT": BTC_MARKET})
        adapter = BinanceAdapter(exchange=fake)

        adapter.fetch_instrument_rules("BTC/USDT")
        adapter.fetch_instrument_rules("BTC/USDT")

        assert fake.load_markets_calls == 1

    def test_maps_market_to_instrument_rules(self) -> None:
        fake = FakeCcxtExchange(markets={"BTC/USDT": BTC_MARKET})
        adapter = BinanceAdapter(exchange=fake)

        rules = adapter.fetch_instrument_rules("BTC/USDT")

        assert rules.symbol == "BTC/USDT"
        assert rules.tick_size == Decimal("0.01")

    def test_unknown_symbol_raises_exchange_adapter_error(self) -> None:
        fake = FakeCcxtExchange(markets={})
        adapter = BinanceAdapter(exchange=fake)

        with pytest.raises(ExchangeAdapterError):
            adapter.fetch_instrument_rules("DOES/NOTEXIST")


class TestUnimplementedLiveMethods:
    def test_place_order_raises_not_implemented(self) -> None:
        adapter = BinanceAdapter(exchange=FakeCcxtExchange())
        with pytest.raises(NotImplementedError):
            adapter.place_order(object())  # type: ignore[arg-type]

    def test_cancel_order_raises_not_implemented(self) -> None:
        adapter = BinanceAdapter(exchange=FakeCcxtExchange())
        with pytest.raises(NotImplementedError):
            adapter.cancel_order("123", "BTC/USDT")

    def test_get_balance_raises_not_implemented(self) -> None:
        adapter = BinanceAdapter(exchange=FakeCcxtExchange())
        with pytest.raises(NotImplementedError):
            adapter.get_balance("USDT")
