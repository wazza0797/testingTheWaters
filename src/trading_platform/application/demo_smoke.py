"""Venue-agnostic demo account pipeclean: open min-size → poll → close → poll.

Uses `IExchangeAdapter` only — works for Binance demo, IG demo, and any future
sandbox venue behind the factory. Never targets live.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from trading_platform.domain.errors import ExchangeAdapterError, TradingPlatformError
from trading_platform.domain.models.exchange_order import ExchangeOrderState, ExchangeOrderStatus
from trading_platform.domain.models.order import Order, OrderSide, OrderType
from trading_platform.domain.ports.exchange import IExchangeAdapter
from trading_platform.exchanges.ig.client import ACCOUNT_CASH_SENTINEL

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DemoSmokeResult:
    exchange: str
    symbol: str
    quantity: Decimal
    open_deal_id: str
    open_status: ExchangeOrderStatus
    close_deal_id: str | None
    close_status: ExchangeOrderStatus | None


def run_demo_smoke(
    adapter: IExchangeAdapter,
    symbol: str,
    *,
    side: OrderSide = OrderSide.BUY,
    close: bool = True,
    poll_interval_sec: float = 1.0,
    max_polls: int = 30,
) -> DemoSmokeResult:
    """Place a minimum-size market order on the demo venue and optionally close it.

    Raises `TradingPlatformError` / `ExchangeAdapterError` on hard failures
    (rejected open, timeout, shorts requested on a long-only instrument).
    """
    if side == OrderSide.SELL:
        rules = adapter.fetch_instrument_rules(symbol)
        if not rules.allows_short:
            raise TradingPlatformError(
                f"{symbol} on {adapter.exchange_name} does not allow short opens "
                f"(allows_short=False). Use --side buy for a long smoke test."
            )
        quantity = rules.min_qty
    else:
        rules = adapter.fetch_instrument_rules(symbol)
        quantity = rules.min_qty

    if quantity <= 0:
        raise TradingPlatformError(
            f"Instrument rules for {symbol} have min_qty={quantity}; cannot smoke-test."
        )

    _require_flat_or_warn(adapter, symbol)

    open_order = _market_order(symbol, side, quantity)
    open_id = adapter.place_order(open_order)
    logger.info(
        "demo_smoke_opened",
        extra={"exchange": adapter.exchange_name, "symbol": symbol, "deal": open_id},
    )
    open_status = _wait_for_terminal(adapter, open_id, symbol, poll_interval_sec, max_polls)
    if open_status.state == ExchangeOrderState.REJECTED:
        detail = open_status.venue_message or "no venue reason returned"
        market_hint = _market_status_hint(adapter, symbol)
        raise ExchangeAdapterError(
            f"Demo smoke open rejected for {symbol} ({adapter.exchange_name}): "
            f"deal={open_id} reason={detail}{market_hint}"
        )
    if open_status.state != ExchangeOrderState.FILLED:
        detail = open_status.venue_message or open_status.state.value
        raise ExchangeAdapterError(
            f"Demo smoke open did not fill in time for {symbol}: "
            f"state={open_status.state.value} deal={open_id} detail={detail}"
        )

    close_id: str | None = None
    close_status: ExchangeOrderStatus | None = None
    if close:
        close_side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY
        close_order = _market_order(symbol, close_side, quantity)
        close_id = adapter.place_order(close_order)
        logger.info(
            "demo_smoke_closing",
            extra={"exchange": adapter.exchange_name, "symbol": symbol, "deal": close_id},
        )
        close_status = _wait_for_terminal(adapter, close_id, symbol, poll_interval_sec, max_polls)
        if close_status.state not in {
            ExchangeOrderState.FILLED,
            ExchangeOrderState.CANCELLED,
        }:
            raise ExchangeAdapterError(
                f"Demo smoke close did not complete for {symbol}: "
                f"state={close_status.state.value} deal={close_id}"
            )

    return DemoSmokeResult(
        exchange=adapter.exchange_name,
        symbol=symbol,
        quantity=quantity,
        open_deal_id=open_id,
        open_status=open_status,
        close_deal_id=close_id,
        close_status=close_status,
    )


def _market_order(symbol: str, side: OrderSide, quantity: Decimal) -> Order:
    return Order(
        order_id=uuid.uuid4().hex,
        correlation_id="demo-smoke",
        symbol=symbol,
        side=side,
        order_type=OrderType.MARKET,
        quantity=quantity,
        price=None,
        strategy_name="demo-smoke",
        created_at=datetime.now(tz=UTC),
    )


def _wait_for_terminal(
    adapter: IExchangeAdapter,
    order_id: str,
    symbol: str,
    poll_interval_sec: float,
    max_polls: int,
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


def _require_flat_or_warn(adapter: IExchangeAdapter, symbol: str) -> None:
    """Refuse to open if we can detect an existing position on this symbol.

    Spot: non-zero base balance. CFD epic: non-zero `get_balance(epic)`.
    """
    try:
        if "/" in symbol:
            base = symbol.split("/", 1)[0]
            qty = adapter.get_balance(base)
        else:
            qty = adapter.get_balance(symbol)
    except ExchangeAdapterError:
        logger.warning("demo_smoke_skip_flat_check", extra={"symbol": symbol})
        return

    if qty != 0:
        raise TradingPlatformError(
            f"Refusing demo smoke: existing position on {symbol} "
            f"(qty={qty}). Flatten in the venue UI first, or use a flat symbol."
        )


def format_smoke_balance_hint(adapter: IExchangeAdapter, symbol: str) -> str:
    """Best-effort cash line for CLI output."""
    try:
        if "/" in symbol:
            quote = symbol.split("/", 1)[1]
            cash = adapter.get_balance(quote)
            return f"{quote} free={cash}"
        cash = adapter.get_balance(ACCOUNT_CASH_SENTINEL)
        return f"ACCOUNT available={cash}"
    except ExchangeAdapterError as exc:
        return f"(balance unavailable: {exc})"


def _market_status_hint(adapter: IExchangeAdapter, symbol: str) -> str:
    status_fn = getattr(adapter, "market_status", None)
    if not callable(status_fn):
        return ""
    try:
        status = status_fn(symbol)
    except ExchangeAdapterError:
        return ""
    if not status:
        return ""
    return f" marketStatus={status}"
