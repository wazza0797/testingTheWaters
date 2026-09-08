from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx

from trading_platform.domain.errors import ExchangeAdapterError
from trading_platform.domain.models.bar import Bar
from trading_platform.domain.models.exchange_order import ExchangeOrderStatus
from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.domain.models.order import Order, OrderSide, OrderType
from trading_platform.exchanges.ig.client import (
    ACCOUNT_CASH_SENTINEL,
    DEMO_BASE_URL,
    LIVE_BASE_URL,
    IgRestClient,
)
from trading_platform.exchanges.ig.mapper import (
    EXCHANGE_NAME,
    map_confirm_to_order_status,
    map_instrument_rules,
    map_price_points,
    map_resolution,
)
from trading_platform.utils.retry import retry_with_backoff

logger = logging.getLogger(__name__)

_MAX_PRICE_POINTS = 500


class IgAdapter:
    """`IExchangeAdapter` for IG Markets CFDs.

    Construct via `for_demo` (working) or `for_live` (scaffolded — factory still
    refuses live trading until Milestone 8b). Demo and live hosts are fixed
    constants; credentials must match the host.
    """

    def __init__(self, client: IgRestClient) -> None:
        self._client = client
        # dealReference → order side at submission (needed for confirm mapping)
        self._deal_sides: dict[str, OrderSide] = {}

    @classmethod
    def for_demo(
        cls,
        *,
        api_key: str | None,
        username: str | None,
        password: str | None,
        account_id: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> IgAdapter:
        if not api_key or not username or not password:
            raise ExchangeAdapterError(
                "ENV=demo with exchange=ig requires IG_DEMO_API_KEY, "
                "IG_DEMO_USERNAME, and IG_DEMO_PASSWORD "
                "(from IG Labs / your demo account — never put live keys here)."
            )
        client = IgRestClient(
            base_url=DEMO_BASE_URL,
            api_key=api_key,
            username=username,
            password=password,
            account_id=account_id,
            http_client=http_client,
        )
        return cls(client)

    @classmethod
    def for_live(
        cls,
        *,
        api_key: str | None,
        username: str | None,
        password: str | None,
        account_id: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> IgAdapter:
        """Scaffold for Milestone 8b — live host + live credentials only.

        The exchange factory must still refuse to use this for trading until
        live execution is explicitly implemented and unlocked.
        """
        if not api_key or not username or not password:
            raise ExchangeAdapterError("Live IG requires IG_API_KEY, IG_USERNAME, and IG_PASSWORD.")
        client = IgRestClient(
            base_url=LIVE_BASE_URL,
            api_key=api_key,
            username=username,
            password=password,
            account_id=account_id,
            http_client=http_client,
        )
        return cls(client)

    @property
    def exchange_name(self) -> str:
        return EXCHANGE_NAME

    @retry_with_backoff(max_attempts=3, base_delay_seconds=1.0, exceptions=(ExchangeAdapterError,))
    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[Bar]:
        resolution = map_resolution(timeframe)
        max_points = min(limit or _MAX_PRICE_POINTS, _MAX_PRICE_POINTS)
        params: dict[str, Any] = {"resolution": resolution, "max": max_points}
        if since is not None:
            params["from"] = since.strftime("%Y-%m-%dT%H:%M:%S")
        payload = self._client.request("GET", f"/prices/{symbol}", version="3", params=params)
        if not isinstance(payload, dict):
            raise ExchangeAdapterError("IG /prices returned a non-object payload")
        return map_price_points(symbol, timeframe, payload)

    @retry_with_backoff(max_attempts=3, base_delay_seconds=1.0, exceptions=(ExchangeAdapterError,))
    def fetch_instrument_rules(self, symbol: str) -> InstrumentRules:
        payload = self._client.request("GET", f"/markets/{symbol}", version="3")
        if not isinstance(payload, dict):
            raise ExchangeAdapterError(f"IG /markets/{symbol} returned a non-object payload")
        return map_instrument_rules(symbol, payload)

    @retry_with_backoff(max_attempts=3, base_delay_seconds=1.0, exceptions=(ExchangeAdapterError,))
    def place_order(self, order: Order) -> str:
        if order.order_type != OrderType.MARKET:
            raise ExchangeAdapterError("IG adapter v1 only supports market orders")

        open_position = self._find_open_position(order.symbol)
        if open_position is not None:
            return self._close_position(order, open_position)

        return self._open_position(order)

    def _open_position(self, order: Order) -> str:
        currency = self._client.account_currency or "GBP"
        direction = "BUY" if order.side == OrderSide.BUY else "SELL"
        body = {
            "epic": order.symbol,
            "expiry": "-",
            "direction": direction,
            "size": float(order.quantity),
            "orderType": "MARKET",
            "currencyCode": currency,
            "forceOpen": True,
            "guaranteedStop": False,
        }
        payload = self._client.request("POST", "/positions/otc", version="2", json_body=body)
        return self._register_deal_reference(payload, order.side)

    def _close_position(self, order: Order, position: dict[str, Any]) -> str:
        deal_id = position.get("dealId")
        if not deal_id:
            raise ExchangeAdapterError(f"Open IG position for {order.symbol} has no dealId")
        # Close with the opposite direction of the open position.
        pos_dir = str(position.get("direction") or "").upper()
        close_dir = "SELL" if pos_dir == "BUY" else "BUY"
        size = position.get("size") or float(order.quantity)
        body = {
            "dealId": deal_id,
            "direction": close_dir,
            "size": float(size),
            "orderType": "MARKET",
        }
        payload = self._client.request("DELETE", "/positions/otc", version="1", json_body=body)
        return self._register_deal_reference(payload, order.side)

    def _register_deal_reference(self, payload: Any, side: OrderSide) -> str:
        if not isinstance(payload, dict):
            raise ExchangeAdapterError("IG deal response was not an object")
        deal_reference = payload.get("dealReference")
        if not deal_reference:
            raise ExchangeAdapterError("IG deal response missing dealReference")
        ref = str(deal_reference)
        self._deal_sides[ref] = side
        return ref

    def _find_open_position(self, epic: str) -> dict[str, Any] | None:
        payload = self._client.request("GET", "/positions", version="2")
        if not isinstance(payload, dict):
            return None
        for entry in payload.get("positions") or []:
            if not isinstance(entry, dict):
                continue
            market = entry.get("market") or {}
            position = entry.get("position") or entry
            if market.get("epic") == epic or position.get("epic") == epic:
                # Flatten dealId/direction/size onto one dict for callers.
                flat = dict(position)
                if "dealId" not in flat and entry.get("position"):
                    flat.update(entry["position"])
                return flat
        return None

    @retry_with_backoff(max_attempts=3, base_delay_seconds=1.0, exceptions=(ExchangeAdapterError,))
    def cancel_order(self, order_id: str, symbol: str) -> None:
        raise ExchangeAdapterError(
            "IG adapter v1 does not cancel working orders (market deals only); "
            f"cannot cancel {order_id} for {symbol}"
        )

    @retry_with_backoff(max_attempts=3, base_delay_seconds=1.0, exceptions=(ExchangeAdapterError,))
    def get_balance(self, asset: str) -> Decimal:
        self._client.ensure_session()
        payload = self._client.request("GET", "/accounts", version="1")
        if not isinstance(payload, dict):
            raise ExchangeAdapterError("IG /accounts returned a non-object payload")

        accounts = payload.get("accounts") or []
        account = self._pick_account(accounts)
        if account is None:
            return Decimal("0")

        balance = account.get("balance") or {}
        if asset == ACCOUNT_CASH_SENTINEL or asset == account.get("currency"):
            available = balance.get("available")
            if available is None:
                available = balance.get("balance")
            return Decimal(str(available or 0))

        # Epic → open position size (signed: short as negative).
        position = self._find_open_position(asset)
        if position is None:
            return Decimal("0")
        size = Decimal(str(position.get("size") or 0))
        direction = str(position.get("direction") or "BUY").upper()
        return -size if direction == "SELL" else size

    def _pick_account(self, accounts: list[Any]) -> dict[str, Any] | None:
        for account in accounts:
            if isinstance(account, dict) and account.get("preferred"):
                return account
        for account in accounts:
            if isinstance(account, dict):
                return account
        return None

    @retry_with_backoff(max_attempts=3, base_delay_seconds=1.0, exceptions=(ExchangeAdapterError,))
    def fetch_order(self, order_id: str, symbol: str) -> ExchangeOrderStatus:
        payload = self._client.request("GET", f"/confirms/{order_id}", version="1")
        if not isinstance(payload, dict):
            raise ExchangeAdapterError(f"IG /confirms/{order_id} returned a non-object payload")
        side = self._deal_sides.get(order_id, OrderSide.BUY)
        status = map_confirm_to_order_status(
            deal_reference=order_id, symbol=symbol, side=side, confirm=payload
        )
        # Keep DemoBroker's open-order key stable: status id may be dealId, but
        # callers poll with the dealReference returned by place_order. Re-stamp
        # exchange_order_id to the reference we were given.
        return ExchangeOrderStatus(
            exchange_order_id=order_id,
            symbol=status.symbol,
            side=status.side,
            order_type=status.order_type,
            state=status.state,
            quantity=status.quantity,
            filled_quantity=status.filled_quantity,
            remaining_quantity=status.remaining_quantity,
            average_fill_price=status.average_fill_price,
            fee=status.fee,
            fee_currency=status.fee_currency,
            timestamp=status.timestamp,
        )
