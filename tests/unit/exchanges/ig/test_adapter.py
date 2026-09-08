from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from trading_platform.config.settings import Environment, Settings
from trading_platform.domain.errors import ConfigurationError, ExchangeAdapterError
from trading_platform.domain.models.exchange_order import ExchangeOrderState
from trading_platform.domain.models.order import Order, OrderSide, OrderType
from trading_platform.exchanges.factory import build_exchange_adapter
from trading_platform.exchanges.ig.adapter import IgAdapter
from trading_platform.exchanges.ig.client import ACCOUNT_CASH_SENTINEL, DEMO_BASE_URL
from trading_platform.exchanges.ig.mapper import map_confirm_to_order_status, map_price_points
from trading_platform.portfolio.seed import seed_book_from_exchange


def _order(side: OrderSide = OrderSide.BUY) -> Order:
    return Order(
        order_id="client-1",
        correlation_id="c1",
        symbol="CS.D.EURUSD.MINI.IP",
        side=side,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        price=None,
        strategy_name="test",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


class _MockTransport(httpx.MockTransport):
    """Route IG REST calls to canned JSON responses."""

    def __init__(self, routes: dict[tuple[str, str], httpx.Response]) -> None:
        self._routes = routes
        super().__init__(self._handler)

    def _handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.replace("/gateway/deal", "")
        key = (request.method.upper(), path)
        if key in self._routes:
            return self._routes[key]
        # Allow session login for any POST /session
        if request.method.upper() == "POST" and path.endswith("/session"):
            return httpx.Response(
                200,
                headers={"CST": "cst-token", "X-SECURITY-TOKEN": "sec-token"},
                json={
                    "currentAccountId": "ABC",
                    "accounts": [
                        {
                            "accountId": "ABC",
                            "currency": "GBP",
                            "preferred": True,
                            "balance": {"available": 10000, "balance": 10000},
                        }
                    ],
                },
            )
        return httpx.Response(404, json={"errorCode": f"unhandled {key}"})


def _client(routes: dict[tuple[str, str], httpx.Response]) -> httpx.Client:
    return httpx.Client(
        transport=_MockTransport(routes),
        base_url=DEMO_BASE_URL,
    )


class TestIgMapper:
    def test_map_price_points_mid(self) -> None:
        payload = {
            "prices": [
                {
                    "snapshotTimeUTC": "2024/01/01 12:00:00",
                    "openPrice": {"bid": 1.0, "ask": 1.2},
                    "highPrice": {"bid": 1.1, "ask": 1.3},
                    "lowPrice": {"bid": 0.9, "ask": 1.1},
                    "closePrice": {"bid": 1.0, "ask": 1.2},
                    "lastTradedVolume": 0,
                }
            ]
        }
        bars = map_price_points("CS.D.EURUSD.MINI.IP", "1h", payload)
        assert len(bars) == 1
        assert bars[0].open == Decimal("1.1")
        assert bars[0].close == Decimal("1.1")

    def test_map_confirm_accepted(self) -> None:
        status = map_confirm_to_order_status(
            deal_reference="ref-1",
            symbol="CS.D.EURUSD.MINI.IP",
            side=OrderSide.BUY,
            confirm={"dealStatus": "ACCEPTED", "size": 1, "level": 1.1, "dealId": "d1"},
        )
        assert status.state is ExchangeOrderState.FILLED
        assert status.filled_quantity == Decimal("1")
        assert status.average_fill_price == Decimal("1.1")


class TestIgAdapterDemo:
    def test_for_demo_requires_credentials(self) -> None:
        with pytest.raises(ExchangeAdapterError, match="IG_DEMO"):
            IgAdapter.for_demo(api_key=None, username="u", password="p")

    def test_fetch_ohlcv_and_place_order(self) -> None:
        routes = {
            ("GET", "/prices/CS.D.EURUSD.MINI.IP"): httpx.Response(
                200,
                json={
                    "prices": [
                        {
                            "snapshotTimeUTC": "2024/01/01 12:00:00",
                            "openPrice": {"bid": 1.1, "ask": 1.1},
                            "highPrice": {"bid": 1.2, "ask": 1.2},
                            "lowPrice": {"bid": 1.0, "ask": 1.0},
                            "closePrice": {"bid": 1.15, "ask": 1.15},
                            "lastTradedVolume": 0,
                        }
                    ]
                },
            ),
            ("GET", "/positions"): httpx.Response(200, json={"positions": []}),
            ("POST", "/positions/otc"): httpx.Response(200, json={"dealReference": "ref-abc"}),
            ("GET", "/confirms/ref-abc"): httpx.Response(
                200,
                json={
                    "dealStatus": "ACCEPTED",
                    "size": 1,
                    "level": 1.15,
                    "dealId": "deal-1",
                },
            ),
            ("GET", "/accounts"): httpx.Response(
                200,
                json={
                    "accounts": [
                        {
                            "accountId": "ABC",
                            "currency": "GBP",
                            "preferred": True,
                            "balance": {"available": "5000", "balance": "5000"},
                        }
                    ]
                },
            ),
            ("GET", "/markets/CS.D.EURUSD.MINI.IP"): httpx.Response(
                200,
                json={
                    "dealingRules": {
                        "minDealSize": {"value": 0.1},
                        "minStepDistance": {"value": 0.1},
                    },
                    "snapshot": {"bid": 1.1, "offer": 1.2},
                },
            ),
        }
        adapter = IgAdapter.for_demo(
            api_key="key",
            username="user",
            password="pass",
            http_client=_client(routes),
        )

        bars = adapter.fetch_ohlcv("CS.D.EURUSD.MINI.IP", "1h", limit=10)
        assert len(bars) == 1
        assert bars[0].close == Decimal("1.15")

        rules = adapter.fetch_instrument_rules("CS.D.EURUSD.MINI.IP")
        assert rules.allows_short is True
        assert rules.exchange == "ig"

        ref = adapter.place_order(_order())
        assert ref == "ref-abc"
        status = adapter.fetch_order(ref, "CS.D.EURUSD.MINI.IP")
        assert status.state is ExchangeOrderState.FILLED
        assert status.average_fill_price == Decimal("1.15")

        assert adapter.get_balance(ACCOUNT_CASH_SENTINEL) == Decimal("5000")

    def test_seed_book_from_cfd_epic(self) -> None:
        routes = {
            ("GET", "/accounts"): httpx.Response(
                200,
                json={
                    "accounts": [
                        {
                            "accountId": "ABC",
                            "currency": "GBP",
                            "preferred": True,
                            "balance": {"available": "8000"},
                        }
                    ]
                },
            ),
            ("GET", "/positions"): httpx.Response(200, json={"positions": []}),
            ("GET", "/prices/CS.D.EURUSD.MINI.IP"): httpx.Response(200, json={"prices": []}),
        }
        adapter = IgAdapter.for_demo(
            api_key="key",
            username="user",
            password="pass",
            http_client=_client(routes),
        )
        book = seed_book_from_exchange(adapter, "CS.D.EURUSD.MINI.IP", timeframe="1h")
        assert book.cash == Decimal("8000")
        assert book.position_for("CS.D.EURUSD.MINI.IP") is None


class TestIgFactory:
    def test_ig_live_refused(self) -> None:
        settings = Settings(_env_file=None)
        with pytest.raises(ConfigurationError, match="not implemented"):
            build_exchange_adapter("ig", Environment.LIVE, settings)

    def test_ig_demo_requires_keys(self) -> None:
        settings = Settings(_env_file=None)
        with pytest.raises(ExchangeAdapterError, match="IG_DEMO"):
            build_exchange_adapter("ig", Environment.DEMO, settings)

    def test_known_exchanges_message_includes_ig(self) -> None:
        settings = Settings(_env_file=None)
        with pytest.raises(ConfigurationError, match="ig"):
            build_exchange_adapter("nope", Environment.DEMO, settings)
