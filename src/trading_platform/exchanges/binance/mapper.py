from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from trading_platform.domain.errors import ExchangeAdapterError
from trading_platform.domain.models.bar import Bar
from trading_platform.domain.models.instrument_rules import InstrumentRules

EXCHANGE_NAME = "binance"


def _to_decimal(value: Any, *, field: str) -> Decimal:
    if value is None:
        raise ExchangeAdapterError(f"Missing required market field: {field}")
    return Decimal(str(value))


def _decimal_places(value: Decimal) -> int:
    """Number of digits after the decimal point, e.g. Decimal('0.01') -> 2."""
    exponent = value.normalize().as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


def map_ohlcv_row(symbol: str, timeframe: str, row: list[Any]) -> Bar:
    """Map a single ccxt OHLCV row: `[timestamp_ms, open, high, low, close, volume]`."""
    timestamp_ms, open_, high, low, close, volume = row
    return Bar(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC),
        open=_to_decimal(open_, field="open"),
        high=_to_decimal(high, field="high"),
        low=_to_decimal(low, field="low"),
        close=_to_decimal(close, field="close"),
        volume=_to_decimal(volume, field="volume"),
    )


def map_ohlcv_rows(symbol: str, timeframe: str, rows: list[list[Any]]) -> list[Bar]:
    return [map_ohlcv_row(symbol, timeframe, row) for row in rows]


def _extract_min_notional(market: dict[str, Any]) -> Decimal:
    """ccxt normalizes Binance's MIN_NOTIONAL/NOTIONAL filter into
    `limits.cost.min` on most versions; fall back to scanning the raw
    `info.filters` (Binance's native format) when that's absent, and finally
    to zero (logged as a missing field by the caller via strict validation
    upstream — a symbol with no discoverable minimum is treated as unbounded).
    """
    cost_min = ((market.get("limits") or {}).get("cost") or {}).get("min")
    if cost_min is not None:
        return Decimal(str(cost_min))

    for filt in (market.get("info") or {}).get("filters") or []:
        if filt.get("filterType") in ("MIN_NOTIONAL", "NOTIONAL"):
            raw = filt.get("minNotional") or filt.get("notional")
            if raw is not None:
                return Decimal(str(raw))

    return Decimal("0")


def map_instrument_rules(symbol: str, market: dict[str, Any]) -> InstrumentRules:
    """Map a ccxt `market` dict (from `exchange.load_markets()[symbol]`) to `InstrumentRules`.

    Assumes ccxt's `TICK_SIZE` precision mode, which Binance uses: `precision.price`/
    `precision.amount` are the tick/step sizes themselves (e.g. `0.01`), not
    decimal-place counts.
    """
    try:
        precision = market["precision"]
        limits = market["limits"]
    except KeyError as exc:
        raise ExchangeAdapterError(
            f"Malformed market metadata for {symbol}: missing {exc}"
        ) from exc

    tick_size = _to_decimal(precision.get("price"), field="precision.price")
    step_size = _to_decimal(precision.get("amount"), field="precision.amount")
    min_qty = _to_decimal((limits.get("amount") or {}).get("min"), field="limits.amount.min")
    min_notional = _extract_min_notional(market)
    maker_fee_rate = _to_decimal(market.get("maker", 0), field="maker")
    taker_fee_rate = _to_decimal(market.get("taker", 0), field="taker")

    return InstrumentRules(
        exchange=EXCHANGE_NAME,
        symbol=symbol,
        tick_size=tick_size,
        step_size=step_size,
        min_qty=min_qty,
        min_notional=min_notional,
        price_precision=_decimal_places(tick_size),
        qty_precision=_decimal_places(step_size),
        maker_fee_rate=maker_fee_rate,
        taker_fee_rate=taker_fee_rate,
    )
