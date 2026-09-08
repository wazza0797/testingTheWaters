"""IG Markets REST client — demo and live hosts, session tokens only.

Application code never imports this; only `IgAdapter` does.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from trading_platform.domain.errors import ExchangeAdapterError

logger = logging.getLogger(__name__)

DEMO_BASE_URL = "https://demo-api.ig.com/gateway/deal"
LIVE_BASE_URL = "https://api.ig.com/gateway/deal"

ACCOUNT_CASH_SENTINEL = "ACCOUNT"


class IgRestClient:
    """Thin httpx wrapper around IG's REST gateway.

    `base_url` must be one of the module constants — never a free-form string
    from env — so demo/live cannot be mixed by accident.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        username: str,
        password: str,
        account_id: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        if base_url not in (DEMO_BASE_URL, LIVE_BASE_URL):
            raise ExchangeAdapterError(
                f"IG base_url must be DEMO_BASE_URL or LIVE_BASE_URL, got {base_url!r}"
            )
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._username = username
        self._password = password
        self._account_id = account_id
        self._cst: str | None = None
        self._security_token: str | None = None
        self._account_currency: str | None = None
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(timeout=30.0)

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def is_demo(self) -> bool:
        return self._base_url == DEMO_BASE_URL

    @property
    def account_currency(self) -> str | None:
        return self._account_currency

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def ensure_session(self) -> None:
        if self._cst and self._security_token:
            return
        self.login()

    def login(self) -> None:
        headers = {
            "X-IG-API-KEY": self._api_key,
            "Version": "2",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        body = {"identifier": self._username, "password": self._password}
        try:
            response = self._http.post(f"{self._base_url}/session", headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise ExchangeAdapterError(f"IG session login network error: {exc}") from exc

        if response.status_code >= 400:
            raise ExchangeAdapterError(
                f"IG session login failed ({response.status_code}): {response.text[:300]}"
            )

        cst = response.headers.get("CST")
        security = response.headers.get("X-SECURITY-TOKEN")
        if not cst or not security:
            raise ExchangeAdapterError(
                "IG session login response missing CST / X-SECURITY-TOKEN "
                "(wrong host for these credentials? demo keys need demo-api.ig.com)"
            )
        self._cst = cst
        self._security_token = security

        payload = response.json()
        self._account_currency = self._pick_currency(payload)
        if self._account_id:
            self._switch_account(self._account_id)

    def _pick_currency(self, payload: dict[str, Any]) -> str | None:
        current = payload.get("currentAccountId")
        for account in payload.get("accounts") or []:
            if not isinstance(account, dict):
                continue
            if current and account.get("accountId") == current:
                currency = account.get("currency")
                return str(currency) if currency else None
        for account in payload.get("accounts") or []:
            if isinstance(account, dict) and account.get("currency"):
                return str(account["currency"])
        return None

    def _switch_account(self, account_id: str) -> None:
        self.request("PUT", "/session", version="1", json_body={"accountId": account_id})

    def request(
        self,
        method: str,
        path: str,
        *,
        version: str = "1",
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        retry_on_auth: bool = True,
    ) -> Any:
        self.ensure_session()
        assert self._cst is not None and self._security_token is not None
        headers = {
            "X-IG-API-KEY": self._api_key,
            "CST": self._cst,
            "X-SECURITY-TOKEN": self._security_token,
            "Version": version,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        url = f"{self._base_url}{path}"
        try:
            response = self._http.request(
                method, url, headers=headers, json=json_body, params=params
            )
        except httpx.HTTPError as exc:
            raise ExchangeAdapterError(f"IG {method} {path} network error: {exc}") from exc

        if response.status_code in {401, 403} and retry_on_auth:
            logger.info("ig_session_expired_relogin", extra={"path": path})
            self._cst = None
            self._security_token = None
            self.login()
            return self.request(
                method,
                path,
                version=version,
                json_body=json_body,
                params=params,
                retry_on_auth=False,
            )

        if response.status_code >= 400:
            raise ExchangeAdapterError(
                f"IG {method} {path} failed ({response.status_code}): {response.text[:400]}"
            )

        if response.status_code == 204 or not response.content:
            return {}
        return response.json()
