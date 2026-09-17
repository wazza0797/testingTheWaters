from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from trading_platform.domain.errors import ExchangeAdapterError
from trading_platform.domain.models.bar import Bar
from trading_platform.domain.models.exchange_order import ExchangeOrderState, ExchangeOrderStatus
from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.domain.models.order import OrderSide, OrderType

EXCHANGE_NAME = "ig"

# Platform timeframe → IG /prices resolution codes.
_RESOLUTION: dict[str, str] = {
    "1m": "MINUTE",
    "5m": "MINUTE_5",
    "15m": "MINUTE_15",
    "1h": "HOUR",
    "4h": "HOUR_4",
    "1d": "DAY",
}


def map_resolution(timeframe: str) -> str:
    try:
        return _RESOLUTION[timeframe]
    except KeyError as exc:
        raise ExchangeAdapterError(
            f"Unsupported IG timeframe {timeframe!r}. Known: {sorted(_RESOLUTION)}"
        ) from exc


def _to_decimal(value: Any, *, field: str) -> Decimal:
    if value is None:
        raise ExchangeAdapterError(f"Missing required IG field: {field}")
    return Decimal(str(value))


def _mid_price(bid: Any, ask: Any, *, field: str) -> Decimal:
    if bid is None and ask is None:
        raise ExchangeAdapterError(f"Missing bid/ask for {field}")
    if bid is None:
        return _to_decimal(ask, field=f"{field}.ask")
    if ask is None:
        return _to_decimal(bid, field=f"{field}.bid")
    return (_to_decimal(bid, field=f"{field}.bid") + _to_decimal(ask, field=f"{field}.ask")) / 2


def _parse_snapshot_time(raw: str) -> datetime:
    # IG returns e.g. "2024/01/15 12:00:00" or ISO variants.
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    raise ExchangeAdapterError(f"Unrecognised IG snapshotTime: {raw!r}")


def map_price_points(symbol: str, timeframe: str, payload: dict[str, Any]) -> list[Bar]:
    bars: list[Bar] = []
    for point in payload.get("prices") or []:
        if not isinstance(point, dict):
            continue
        open_px = point.get("openPrice") or {}
        high_px = point.get("highPrice") or {}
        low_px = point.get("lowPrice") or {}
        close_px = point.get("closePrice") or {}
        snapshot = point.get("snapshotTimeUTC") or point.get("snapshotTime")
        if not snapshot:
            continue
        volume_raw = point.get("lastTradedVolume")
        volume = Decimal(str(volume_raw)) if volume_raw is not None else Decimal("0")
        bars.append(
            Bar(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=_parse_snapshot_time(str(snapshot)),
                open=_mid_price(open_px.get("bid"), open_px.get("ask"), field="openPrice"),
                high=_mid_price(high_px.get("bid"), high_px.get("ask"), field="highPrice"),
                low=_mid_price(low_px.get("bid"), low_px.get("ask"), field="lowPrice"),
                close=_mid_price(close_px.get("bid"), close_px.get("ask"), field="closePrice"),
                volume=volume,
            )
        )
    bars.sort(key=lambda bar: bar.timestamp)
    return bars


def _decimal_places(value: Decimal) -> int:
    exponent = value.normalize().as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


def map_instrument_rules(symbol: str, payload: dict[str, Any]) -> InstrumentRules:
    dealing = payload.get("dealingRules") or {}
    min_deal = dealing.get("minDealSize") or {}
    min_size = _to_decimal(min_deal.get("value", "1"), field="minDealSize.value")
    if min_size <= 0:
        min_size = Decimal("1")
    # Quantity increment: IG's API does not expose a dedicated size-step field.
    # `minStepDistance` is a *price* stop distance in points — do NOT use it as
    # qty step (it commonly dwarfs minDealSize, e.g. min=0.04 / stepDist=1.0).
    step = min_size
    # CFDs quote in points; use a fine tick default when snapshot has no scale.
    tick = Decimal("0.0001")
    snapshot = payload.get("snapshot") or {}
    for key in ("bid", "offer", "high", "low"):
        raw = snapshot.get(key)
        if raw is not None:
            as_dec = Decimal(str(raw))
            places = _decimal_places(as_dec)
            if places > 0:
                tick = Decimal(1).scaleb(-places)
            break

    # Fees are not a simple maker/taker % on IG CFDs — use zero and document.
    return InstrumentRules(
        exchange=EXCHANGE_NAME,
        symbol=symbol,
        tick_size=tick,
        step_size=step,
        min_qty=min_size,
        min_notional=Decimal("0"),
        price_precision=_decimal_places(tick),
        qty_precision=_decimal_places(step),
        maker_fee_rate=Decimal("0"),
        taker_fee_rate=Decimal("0"),
        allows_short=True,
    )


def pick_dealing_currency(
    market_payload: dict[str, Any], account_currency: str | None = None
) -> str:
    """currencyCode must be an *instrument* dealing currency, not account cash currency.

    Using account GBP on a USD-dealt EURUSD epic yields IG rejects like UNKNOWN /
    "failed to retrieve price information for the currency".
    """
    instrument = market_payload.get("instrument") or {}
    codes: list[str] = []
    for entry in instrument.get("currencies") or []:
        if isinstance(entry, dict) and entry.get("code"):
            codes.append(str(entry["code"]))
    if not codes:
        raise ExchangeAdapterError(
            f"IG market {instrument.get('epic')!r} has no instrument.currencies; "
            "cannot choose currencyCode for /positions/otc"
        )
    if account_currency and account_currency in codes:
        return account_currency
    if "USD" in codes:
        return "USD"
    return codes[0]


def pick_expiry(market_payload: dict[str, Any]) -> str:
    """CFDs use '-'; daily spread bets usually 'DFB'; forwards use a date code."""
    instrument = market_payload.get("instrument") or {}
    expiry = instrument.get("expiry")
    if expiry is not None and str(expiry).strip() != "":
        return str(expiry)
    itype = str(instrument.get("type") or instrument.get("instrumentType") or "").upper()
    if "SPREADBET" in itype or itype in {"BINARY", "OPT_COMMODITY", "OPT_FX", "OPT_INDEX"}:
        return "DFB"
    return "-"


def build_open_position_body(
    *,
    epic: str,
    direction: str,
    size: float,
    market_payload: dict[str, Any],
    account_currency: str | None,
) -> dict[str, Any]:
    """Build POST /positions/otc v2 body with IG's expected fields.

    Omits null optional keys — IG rejects `validation.null-not-allowed` when
    nulls are sent for fields that must be absent instead.
    """
    body: dict[str, Any] = {
        "epic": epic,
        "expiry": pick_expiry(market_payload),
        "direction": direction,
        "size": size,
        "orderType": "MARKET",
        "timeInForce": "FILL_OR_KILL",
        "currencyCode": pick_dealing_currency(market_payload, account_currency),
        "forceOpen": True,
        "guaranteedStop": False,
        "trailingStop": False,
    }
    return body


def build_close_position_body(
    *,
    deal_id: str,
    direction: str,
    size: float,
) -> dict[str, Any]:
    """Build close body for DELETE /positions/otc Version 1 (CloseOTCPositionV1).

    Identify the position by **dealId only**. Sending dealId together with
    epic/expiry yields `validation.mutual-exclusive-value.request`.

    Market close fields: dealId, direction (opposite of open), size, orderType.
    Omit level/quoteId for MARKET. timeInForce defaults to FILL_OR_KILL on IG.

    Transport must send this as POST + `_method: DELETE` — a real DELETE drops
    the body on IG's gateway (FAQ).
    """
    return {
        "dealId": deal_id,
        "direction": direction,
        "size": size,
        "orderType": "MARKET",
        "timeInForce": "FILL_OR_KILL",
    }


def map_confirm_to_order_status(
    *,
    deal_reference: str,
    symbol: str,
    side: OrderSide,
    confirm: dict[str, Any],
) -> ExchangeOrderStatus:
    """Map GET /confirms/{dealReference} into `ExchangeOrderStatus`."""
    status = str(confirm.get("dealStatus") or "").upper()
    # `size` is deal size; `level` is fill price — never use level as a size fallback.
    size_raw = confirm.get("size")
    size = _to_decimal(size_raw if size_raw is not None else 0, field="size")
    filled = size
    level = confirm.get("level")
    avg = _to_decimal(level, field="level") if level is not None else None
    venue_message: str | None = None
    parts: list[str] = []
    reason = confirm.get("reason")
    if reason is not None and str(reason).strip():
        parts.append(str(reason))
    error_code = confirm.get("errorCode")
    if error_code is not None and str(error_code) not in parts:
        parts.append(f"errorCode={error_code}")
    # Human-facing detail IG sometimes puts alongside UNKNOWN.
    for key in ("rejectReason", "message", "errorMessage"):
        extra = confirm.get(key)
        if extra is not None and str(extra) not in parts:
            parts.append(str(extra))
    if parts:
        venue_message = "; ".join(parts)

    if status in {"ACCEPTED", "OPEN", "FULLY_CLOSED"}:
        state = ExchangeOrderState.FILLED
        remaining = Decimal("0")
    elif status in {"REJECTED", "DISCARDED"}:
        state = ExchangeOrderState.REJECTED
        filled = Decimal("0")
        remaining = size
        avg = None
    else:
        state = ExchangeOrderState.OPEN
        remaining = size

    ts_raw = confirm.get("date")
    if isinstance(ts_raw, str) and ts_raw:
        try:
            timestamp = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
        except ValueError:
            timestamp = datetime.now(tz=UTC)
    else:
        timestamp = datetime.now(tz=UTC)

    deal_id = confirm.get("dealId") or deal_reference
    return ExchangeOrderStatus(
        exchange_order_id=str(deal_id),
        symbol=symbol,
        side=side,
        order_type=OrderType.MARKET,
        state=state,
        quantity=size if size > 0 else filled,
        filled_quantity=filled if state == ExchangeOrderState.FILLED else Decimal("0"),
        remaining_quantity=remaining,
        average_fill_price=avg,
        fee=Decimal("0"),
        fee_currency=None,
        timestamp=timestamp,
        venue_message=venue_message,
    )
