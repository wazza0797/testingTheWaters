from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any, ParamSpec, TypeVar

import httpx

from trading_platform.domain.errors import ExchangeAdapterError, ExchangeRateLimitError
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
    build_close_position_body,
    build_open_position_body,
    map_confirm_to_order_status,
    map_instrument_rules,
    map_price_points,
    map_resolution,
)
from trading_platform.utils.retry import retry_with_backoff

logger = logging.getLogger(__name__)

_MAX_PRICE_POINTS = 500

_P = ParamSpec("_P")
_T = TypeVar("_T")


def _ig_retry(func: Callable[_P, _T]) -> Callable[_P, _T]:
    """Retry transient IG errors; never hammer allowance / rate-limit failures."""
    return retry_with_backoff(
        max_attempts=3,
        base_delay_seconds=1.0,
        exceptions=(ExchangeAdapterError,),
        exclude=(ExchangeRateLimitError,),
    )(func)


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

    @_ig_retry
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

    @_ig_retry
    def fetch_instrument_rules(self, symbol: str) -> InstrumentRules:
        payload = self._client.request("GET", f"/markets/{symbol}", version="3")
        if not isinstance(payload, dict):
            raise ExchangeAdapterError(f"IG /markets/{symbol} returned a non-object payload")
        return map_instrument_rules(symbol, payload)

    def place_order(self, order: Order) -> str:
        # Intentionally not retried: open/close are not idempotent without a
        # client deal key — automatic retries can duplicate venue positions.
        if order.order_type != OrderType.MARKET:
            raise ExchangeAdapterError("IG adapter v1 only supports market orders")

        open_position = self._find_open_position(order.symbol)
        if open_position is not None:
            return self._close_position(order, open_position)

        return self._open_position(order)

    def _open_position(self, order: Order) -> str:
        market = self._client.request("GET", f"/markets/{order.symbol}", version="3")
        if not isinstance(market, dict):
            raise ExchangeAdapterError(f"IG /markets/{order.symbol} returned a non-object payload")
        direction = "BUY" if order.side == OrderSide.BUY else "SELL"
        body = build_open_position_body(
            epic=order.symbol,
            direction=direction,
            size=float(order.quantity),
            market_payload=market,
            account_currency=self._client.account_currency,
        )
        logger.info(
            "ig_open_position",
            extra={
                "epic": order.symbol,
                "currencyCode": body.get("currencyCode"),
                "expiry": body.get("expiry"),
                "size": body.get("size"),
                "direction": direction,
            },
        )
        payload = self._client.request(
            "POST", "/positions/otc", version="2", json_body=body, retry_on_auth=False
        )
        return self._register_deal_reference(payload, order.side)

    def _close_position(self, order: Order, position: dict[str, Any]) -> str:
        deal_id = position.get("dealId")
        if not deal_id:
            raise ExchangeAdapterError(f"Open IG position for {order.symbol} has no dealId")
        # Close with the opposite direction of the open position.
        pos_dir = str(position.get("direction") or "").upper()
        close_dir = "SELL" if pos_dir == "BUY" else "BUY"
        size_raw = position.get("size")
        size = float(size_raw) if size_raw is not None else float(order.quantity)
        body = build_close_position_body(
            deal_id=str(deal_id),
            direction=close_dir,
            size=size,
        )
        logger.info(
            "ig_close_position",
            extra={
                "dealId": deal_id,
                "epic": position.get("epic") or order.symbol,
                "direction": close_dir,
                "size": size,
            },
        )
        payload = self._client.request(
            "DELETE", "/positions/otc", version="1", json_body=body, retry_on_auth=False
        )
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
                flat = dict(position)
                if "dealId" not in flat and isinstance(entry.get("position"), dict):
                    flat.update(entry["position"])
                # Keep epic for logging / balance lookups; close uses dealId only.
                flat.setdefault("epic", market.get("epic") or epic)
                return flat
        return None

    @_ig_retry
    def cancel_order(self, order_id: str, symbol: str) -> None:
        raise ExchangeAdapterError(
            "IG adapter v1 does not cancel working orders (market deals only); "
            f"cannot cancel {order_id} for {symbol}"
        )

    @_ig_retry
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

    @_ig_retry
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
            venue_message=status.venue_message,
        )

    def market_status(self, symbol: str) -> str | None:
        """Best-effort IG snapshot marketStatus (TRADEABLE / CLOSED / …)."""
        try:
            payload = self._client.request("GET", f"/markets/{symbol}", version="3")
        except ExchangeAdapterError:
            return None
        if not isinstance(payload, dict):
            return None
        snapshot = payload.get("snapshot") or {}
        status = snapshot.get("marketStatus")
        return str(status) if status is not None else None
