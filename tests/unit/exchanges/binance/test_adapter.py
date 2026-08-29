from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import ccxt
import pytest

from trading_platform.domain.errors import ExchangeAdapterError
from trading_platform.domain.models.order import Order, OrderSide, OrderType
from trading_platform.exchanges.binance.adapter import BinanceAdapter


class FakeCcxtExchange:
    """Duck-typed stand-in for `ccxt.binance` — no network, no real ccxt calls."""

    def __init__(
        self,
        markets: dict[str, Any] | None = None,
        ohlcv_rows: list[list[Any]] | None = None,
        ohlcv_error: Exception | None = None,
        *,
        create_order_result: dict[str, Any] | None = None,
        fetch_order_result: dict[str, Any] | None = None,
        balance: dict[str, Any] | None = None,
    ) -> None:
        self.markets = markets or {}
        self._ohlcv_rows = ohlcv_rows or []
        self._ohlcv_error = ohlcv_error
        self.load_markets_calls = 0
        self.fetch_ohlcv_calls: list[dict[str, Any]] = []
        self.create_order_calls: list[dict[str, Any]] = []
        self._create_order_result = create_order_result or {"id": "ex-1"}
        self._fetch_order_result = fetch_order_result
        self._balance = balance or {"free": {"USDT": 1000, "BTC": 0}}

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

    def create_order(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.create_order_calls.append(
            {
                "symbol": symbol,
                "type": type,
                "side": side,
                "amount": amount,
                "price": price,
                "params": params or {},
            }
        )
        return dict(self._create_order_result)

    def cancel_order(
        self, id: str, symbol: str | None = None, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {"id": id}

    def fetch_order(
        self, id: str, symbol: str | None = None, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self._fetch_order_result is not None:
            return dict(self._fetch_order_result)
        return {
            "id": id,
            "symbol": symbol or "BTC/USDT",
            "side": "buy",
            "type": "market",
            "status": "closed",
            "amount": 0.01,
            "filled": 0.01,
            "remaining": 0,
            "average": 100,
            "timestamp": 1704067200000,
            "fee": {"cost": 0.01, "currency": "USDT"},
        }

    def fetch_balance(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._balance

    def enable_demo_trading(self, enabled: bool) -> None:
        self.demo_enabled = enabled


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


class TestTradingMethods:
    def test_place_order_returns_exchange_id(self) -> None:
        fake = FakeCcxtExchange(markets={"BTC/USDT": BTC_MARKET})
        adapter = BinanceAdapter(exchange=fake)
        order = Order(
            order_id="c1",
            correlation_id="r1",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.01"),
            price=None,
            strategy_name="t",
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        assert adapter.place_order(order) == "ex-1"
        assert fake.create_order_calls[0]["symbol"] == "BTC/USDT"

    def test_get_balance_reads_free(self) -> None:
        fake = FakeCcxtExchange(balance={"free": {"USDT": "123.45"}})
        adapter = BinanceAdapter(exchange=fake)
        assert adapter.get_balance("USDT") == Decimal("123.45")

    def test_fetch_order_maps_status(self) -> None:
        fake = FakeCcxtExchange()
        adapter = BinanceAdapter(exchange=fake)
        status = adapter.fetch_order("ex-1", "BTC/USDT")
        assert status.exchange_order_id == "ex-1"
        assert status.filled_quantity == Decimal("0.01")

    def test_for_demo_requires_credentials(self) -> None:
        with pytest.raises(ExchangeAdapterError, match="BINANCE_DEMO"):
            BinanceAdapter.for_demo(api_key=None, api_secret=None)
