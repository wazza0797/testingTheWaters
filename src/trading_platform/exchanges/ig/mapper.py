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
    min_step = dealing.get("minStepDistance") or {}
    min_size = _to_decimal(min_deal.get("value", "1"), field="minDealSize.value")
    step = _to_decimal(min_step.get("value", min_size), field="minStepDistance.value")
    if step <= 0:
        step = min_size if min_size > 0 else Decimal("1")
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


def map_confirm_to_order_status(
    *,
    deal_reference: str,
    symbol: str,
    side: OrderSide,
    confirm: dict[str, Any],
) -> ExchangeOrderStatus:
    """Map GET /confirms/{dealReference} into `ExchangeOrderStatus`."""
    status = str(confirm.get("dealStatus") or "").upper()
    size = _to_decimal(confirm.get("size") or confirm.get("level") or 0, field="size")
    # Prefer explicit size; some confirms use `size` for filled amount.
    filled = size
    level = confirm.get("level")
    avg = _to_decimal(level, field="level") if level is not None else None

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
    )
