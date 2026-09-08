from __future__ import annotations

import time

import httpx
import pytest

from trading_platform.domain.errors import ExchangeRateLimitError
from trading_platform.exchanges.ig.client import DEMO_BASE_URL, IgRestClient
from trading_platform.exchanges.ig.rate_limit import (
    DEMO_MIN_INTERVAL_SEC,
    IgRateLimiter,
    is_ig_allowance_error,
)


class TestIgRateLimiter:
    def test_demo_interval_is_conservative(self) -> None:
        assert DEMO_MIN_INTERVAL_SEC >= 2.5
        limiter = IgRateLimiter.for_demo()
        assert limiter.min_interval_sec == DEMO_MIN_INTERVAL_SEC

    def test_wait_spaces_calls(self) -> None:
        limiter = IgRateLimiter(min_interval_sec=0.05)
        t0 = time.monotonic()
        limiter.wait()
        limiter.wait()
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.04


class TestAllowanceDetection:
    def test_detects_api_key_allowance(self) -> None:
        assert is_ig_allowance_error(
            '{"errorCode":"error.public-api.exceeded-api-key-allowance"}'
        )

    def test_ignores_unrelated_errors(self) -> None:
        assert not is_ig_allowance_error('{"errorCode":"validation.null-not-allowed.request"}')


class TestIgClientRateLimitErrors:
    def test_allowance_403_does_not_relogin(self) -> None:
        logins = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path.replace("/gateway/deal", "")
            if request.method.upper() == "POST" and path.endswith("/session"):
                logins["n"] += 1
                return httpx.Response(
                    200,
                    headers={"CST": "cst", "X-SECURITY-TOKEN": "sec"},
                    json={"currencyIsoCode": "GBP", "accounts": []},
                )
            return httpx.Response(
                403,
                json={"errorCode": "error.public-api.exceeded-api-key-allowance"},
            )

        client = IgRestClient(
            base_url=DEMO_BASE_URL,
            api_key="k",
            username="u",
            password="p",
            http_client=httpx.Client(
                transport=httpx.MockTransport(handler),
                base_url=DEMO_BASE_URL,
            ),
        )
        with pytest.raises(ExchangeRateLimitError, match="rate-limited"):
            client.request("GET", "/accounts", version="1")
        assert logins["n"] == 1  # login once; no allowance-triggered re-login

    def test_production_client_enables_demo_limiter(self) -> None:
        # No injected http_client → real pacing for demo host.
        # We only construct; do not call the network.
        client = IgRestClient(
            base_url=DEMO_BASE_URL,
            api_key="k",
            username="u",
            password="p",
        )
        try:
            assert client.rate_limiter is not None
            assert client.rate_limiter.min_interval_sec == DEMO_MIN_INTERVAL_SEC
        finally:
            client.close()
