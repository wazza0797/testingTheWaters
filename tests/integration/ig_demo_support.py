"""Shared helpers for live IG demo network tests.

Gating (all must pass or the suite skips):
- pytest marker ``network`` (CI / default local runs use ``-m "not network"``)
- ``IG_DEMO_API_KEY``, ``IG_DEMO_USERNAME``, ``IG_DEMO_PASSWORD`` in env / ``.env``
- Dealing (open/close) additionally requires ``IG_DEMO_INTEGRATION=1``

Epic under test: ``IG_DEMO_EPIC`` or default ``CS.D.EURUSD.MINI.IP``.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_platform.config.settings import Environment, Settings
from trading_platform.domain.errors import ExchangeAdapterError
from trading_platform.domain.models.exchange_order import ExchangeOrderState, ExchangeOrderStatus
from trading_platform.domain.models.order import Order, OrderSide, OrderType
from trading_platform.exchanges.factory import build_exchange_adapter
from trading_platform.exchanges.ig.adapter import IgAdapter
from trading_platform.exchanges.ig.client import ACCOUNT_CASH_SENTINEL

DEFAULT_EPIC = "CS.D.EURUSD.MINI.IP"
INTEGRATION_ENV = "IG_DEMO_INTEGRATION"


def ig_demo_credentials_available() -> bool:
    settings = Settings()
    return bool(
        settings.ig_demo_api_key and settings.ig_demo_username and settings.ig_demo_password
    )


def dealing_enabled() -> bool:
    return os.environ.get(INTEGRATION_ENV, "").strip() == "1"


def require_ig_demo_credentials() -> Settings:
    settings = Settings()
    if not (settings.ig_demo_api_key and settings.ig_demo_username and settings.ig_demo_password):
        pytest.skip(
            "IG demo credentials missing "
            "(set IG_DEMO_API_KEY / IG_DEMO_USERNAME / IG_DEMO_PASSWORD)"
        )
    return settings


def require_dealing_enabled() -> None:
    if not dealing_enabled():
        pytest.skip(f"Dealing tests gated: set {INTEGRATION_ENV}=1 to place demo open/close orders")


def epic_under_test() -> str:
    return os.environ.get("IG_DEMO_EPIC", DEFAULT_EPIC).strip() or DEFAULT_EPIC


def build_demo_adapter(settings: Settings | None = None) -> IgAdapter:
    settings = settings or require_ig_demo_credentials()
    adapter = build_exchange_adapter("ig", Environment.DEMO, settings)
    assert isinstance(adapter, IgAdapter)
    return adapter


def market_order(symbol: str, side: OrderSide, quantity: Decimal) -> Order:
    return Order(
        order_id=uuid.uuid4().hex,
        correlation_id="ig-integration",
        symbol=symbol,
        side=side,
        order_type=OrderType.MARKET,
        quantity=quantity,
        price=None,
        strategy_name="ig-integration",
        created_at=datetime.now(tz=UTC),
    )


def wait_for_terminal(
    adapter: IgAdapter,
    order_id: str,
    symbol: str,
    *,
    poll_interval_sec: float = 2.5,
    max_polls: int = 30,
) -> ExchangeOrderStatus:
    last: ExchangeOrderStatus | None = None
    for _ in range(max_polls):
        last = adapter.fetch_order(order_id, symbol)
        if last.state in {
            ExchangeOrderState.FILLED,
            ExchangeOrderState.REJECTED,
            ExchangeOrderState.CANCELLED,
        }:
            return last
        time.sleep(poll_interval_sec)
    assert last is not None
    return last


def require_tradeable(adapter: IgAdapter, epic: str) -> None:
    status = adapter.market_status(epic)
    if status is not None and status.upper() != "TRADEABLE":
        pytest.skip(f"IG market {epic} is not TRADEABLE (marketStatus={status})")


def position_qty(adapter: IgAdapter, epic: str) -> Decimal:
    return adapter.get_balance(epic)


def flatten_epic(adapter: IgAdapter, epic: str) -> None:
    """Best-effort close of any open position on ``epic`` (opposite market order)."""
    qty = position_qty(adapter, epic)
    if qty == 0:
        return
    side = OrderSide.SELL if qty > 0 else OrderSide.BUY
    size = abs(qty)
    ref = adapter.place_order(market_order(epic, side, size))
    status = wait_for_terminal(adapter, ref, epic)
    if status.state != ExchangeOrderState.FILLED:
        raise ExchangeAdapterError(
            f"Failed to flatten {epic}: state={status.state.value} "
            f"venue={status.venue_message!r} deal={ref}"
        )


def assert_filled(status: ExchangeOrderStatus, *, context: str) -> None:
    if status.state == ExchangeOrderState.REJECTED:
        raise AssertionError(f"{context} rejected: venue_message={status.venue_message!r}")
    assert status.state == ExchangeOrderState.FILLED, (
        f"{context} expected FILLED, got {status.state.value} venue={status.venue_message!r}"
    )


# Re-export sentinel for tests
__all__ = [
    "ACCOUNT_CASH_SENTINEL",
    "DEFAULT_EPIC",
    "INTEGRATION_ENV",
    "assert_filled",
    "build_demo_adapter",
    "dealing_enabled",
    "epic_under_test",
    "flatten_epic",
    "ig_demo_credentials_available",
    "market_order",
    "position_qty",
    "require_dealing_enabled",
    "require_ig_demo_credentials",
    "require_tradeable",
    "wait_for_terminal",
]
