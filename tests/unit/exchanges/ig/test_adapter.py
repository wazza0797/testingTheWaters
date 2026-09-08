from __future__ import annotations

import json
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
from trading_platform.exchanges.ig.mapper import (
    build_open_position_body,
    map_confirm_to_order_status,
    map_price_points,
    pick_dealing_currency,
    pick_expiry,
)
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

    def test_map_confirm_rejected_includes_reason(self) -> None:
        status = map_confirm_to_order_status(
            deal_reference="ref-1",
            symbol="CS.D.EURUSD.MINI.IP",
            side=OrderSide.BUY,
            confirm={"dealStatus": "REJECTED", "reason": "MARKET_CLOSED", "size": None},
        )
        assert status.state is ExchangeOrderState.REJECTED
        assert status.venue_message == "MARKET_CLOSED"
        assert status.quantity == Decimal("0")

    def test_map_confirm_does_not_use_level_as_size(self) -> None:
        status = map_confirm_to_order_status(
            deal_reference="ref-1",
            symbol="CS.D.EURUSD.MINI.IP",
            side=OrderSide.BUY,
            confirm={"dealStatus": "ACCEPTED", "level": 1.2345, "dealId": "d1"},
        )
        assert status.filled_quantity == Decimal("0")
        assert status.average_fill_price == Decimal("1.2345")

    def test_pick_dealing_currency_prefers_instrument_not_account_when_missing(self) -> None:
        market = {
            "instrument": {
                "epic": "CS.D.EURUSD.MINI.IP",
                "currencies": [{"code": "USD"}, {"code": "EUR"}],
            }
        }
        # Account is GBP (not in instrument list) → must use USD, not GBP.
        assert pick_dealing_currency(market, "GBP") == "USD"

    def test_pick_dealing_currency_uses_account_when_listed(self) -> None:
        market = {"instrument": {"currencies": [{"code": "USD"}, {"code": "GBP"}]}}
        assert pick_dealing_currency(market, "GBP") == "GBP"

    def test_pick_expiry_cfd_dash(self) -> None:
        assert pick_expiry({"instrument": {"expiry": "-", "type": "CURRENCIES"}}) == "-"

    def test_pick_expiry_spreadbet_dfb(self) -> None:
        assert pick_expiry({"instrument": {"type": "SPREADBET"}}) == "DFB"

    def test_build_open_position_body_uses_instrument_currency(self) -> None:
        body = build_open_position_body(
            epic="CS.D.EURUSD.MINI.IP",
            direction="BUY",
            size=0.1,
            market_payload={
                "instrument": {
                    "expiry": "-",
                    "type": "CURRENCIES",
                    "currencies": [{"code": "USD"}],
                }
            },
            account_currency="GBP",
        )
        assert body["currencyCode"] == "USD"
        assert body["expiry"] == "-"
        assert body["timeInForce"] == "FILL_OR_KILL"
        assert body["orderType"] == "MARKET"
        assert "level" not in body  # nulls must be omitted

    def test_build_close_position_body_uses_deal_id_not_epic(self) -> None:
        from trading_platform.exchanges.ig.mapper import build_close_position_body

        body = build_close_position_body(
            deal_id="DIAAA",
            direction="SELL",
            size=0.1,
        )
        assert body == {
            "dealId": "DIAAA",
            "direction": "SELL",
            "size": 0.1,
            "orderType": "MARKET",
            "timeInForce": "FILL_OR_KILL",
        }
        assert "epic" not in body
        assert "expiry" not in body
        assert None not in body.values()


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
                    "instrument": {
                        "epic": "CS.D.EURUSD.MINI.IP",
                        "expiry": "-",
                        "type": "CURRENCIES",
                        "currencies": [{"code": "USD"}, {"code": "EUR"}],
                    },
                    "dealingRules": {
                        "minDealSize": {"value": 0.1},
                        "minStepDistance": {"value": 0.1},
                    },
                    "snapshot": {"bid": 1.1, "offer": 1.2, "marketStatus": "TRADEABLE"},
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

    def test_close_position_tunnels_delete_as_post_with_method_header(self) -> None:
        """Regression: IG drops DELETE bodies → validation.null-not-allowed.

        Closing must hit the wire as POST /positions/otc with `_method: DELETE`
        and a non-null JSON body (dealId/direction/size/orderType/…).
        """
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path.replace("/gateway/deal", "")
            method = request.method.upper()
            if method == "POST" and path.endswith("/session"):
                return httpx.Response(
                    200,
                    headers={"CST": "cst", "X-SECURITY-TOKEN": "sec"},
                    json={
                        "currentAccountId": "ABC",
                        "accounts": [{"accountId": "ABC", "currency": "GBP", "preferred": True}],
                    },
                )
            if method == "GET" and path == "/positions":
                return httpx.Response(
                    200,
                    json={
                        "positions": [
                            {
                                "position": {
                                    "dealId": "DIAAA001",
                                    "direction": "BUY",
                                    "size": 0.1,
                                },
                                "market": {
                                    "epic": "CS.D.EURUSD.MINI.IP",
                                    "expiry": "-",
                                },
                            }
                        ]
                    },
                )
            if path == "/positions/otc":
                seen["method"] = method
                seen["_method"] = request.headers.get("_method")
                seen["version"] = request.headers.get("version")
                seen["body"] = json.loads(request.content.decode()) if request.content else None
                return httpx.Response(200, json={"dealReference": "ref-close"})
            return httpx.Response(404, json={"errorCode": f"unhandled {method} {path}"})

        adapter = IgAdapter.for_demo(
            api_key="key",
            username="user",
            password="pass",
            http_client=httpx.Client(
                transport=httpx.MockTransport(handler),
                base_url=DEMO_BASE_URL,
            ),
        )
        ref = adapter.place_order(_order(OrderSide.SELL))
        assert ref == "ref-close"
        assert seen["method"] == "POST"
        assert seen["_method"] == "DELETE"
        assert seen["version"] == "1"
        body = seen["body"]
        assert isinstance(body, dict)
        assert body["dealId"] == "DIAAA001"
        assert body["direction"] == "SELL"
        assert body["size"] == 0.1
        assert body["orderType"] == "MARKET"
        assert "epic" not in body
        assert "expiry" not in body
        assert None not in body.values()
        assert "level" not in body
        assert "quoteId" not in body

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


class TestIgSessionCurrency:
    def test_login_reads_currency_iso_code_from_real_session_shape(self) -> None:
        """Real IG v2 login puts currencyIsoCode at top level; accounts omit currency."""

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path.replace("/gateway/deal", "")
            if request.method.upper() == "POST" and path.endswith("/session"):
                return httpx.Response(
                    200,
                    headers={"CST": "cst", "X-SECURITY-TOKEN": "sec"},
                    json={
                        "currentAccountId": "ABC",
                        "currencyIsoCode": "GBP",
                        "accounts": [
                            {
                                "accountId": "ABC",
                                "accountName": "Demo",
                                "preferred": True,
                                "accountType": "CFD",
                            }
                        ],
                    },
                )
            return httpx.Response(404, json={"errorCode": f"unhandled {path}"})

        from trading_platform.exchanges.ig.client import IgRestClient

        ig = IgRestClient(
            base_url=DEMO_BASE_URL,
            api_key="k",
            username="u",
            password="p",
            http_client=httpx.Client(
                transport=httpx.MockTransport(handler),
                base_url=DEMO_BASE_URL,
            ),
        )
        ig.login()
        assert ig.account_currency == "GBP"


class TestIgDeleteWithBody:
    def test_delete_with_body_uses_post_and_method_header(self) -> None:
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path.replace("/gateway/deal", "")
            if request.method.upper() == "POST" and path.endswith("/session"):
                return httpx.Response(
                    200,
                    headers={"CST": "cst", "X-SECURITY-TOKEN": "sec"},
                    json={"accounts": [{"currency": "GBP", "preferred": True}]},
                )
            seen["method"] = request.method.upper()
            seen["path"] = path
            seen["_method"] = request.headers.get("_method")
            seen["body"] = json.loads(request.content.decode()) if request.content else None
            return httpx.Response(200, json={"dealReference": "ref-close"})

        client = httpx.Client(transport=httpx.MockTransport(handler), base_url=DEMO_BASE_URL)
        from trading_platform.exchanges.ig.client import IgRestClient

        ig = IgRestClient(
            base_url=DEMO_BASE_URL,
            api_key="k",
            username="u",
            password="p",
            http_client=client,
        )
        out = ig.request(
            "DELETE",
            "/positions/otc",
            version="1",
            json_body={
                "dealId": "D1",
                "direction": "SELL",
                "size": 0.1,
                "orderType": "MARKET",
                "timeInForce": "FILL_OR_KILL",
            },
        )
        assert out == {"dealReference": "ref-close"}
        assert seen["method"] == "POST"
        assert seen["_method"] == "DELETE"
        assert seen["path"] == "/positions/otc"
        assert isinstance(seen["body"], dict)
        assert seen["body"]["dealId"] == "D1"
        assert None not in seen["body"].values()


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
