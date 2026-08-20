from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from trading_platform.domain.errors import ExchangeAdapterError
from trading_platform.exchanges.binance.mapper import (
    map_instrument_rules,
    map_ohlcv_row,
    map_ohlcv_rows,
)

FIXTURE_PATH = Path("tests/fixtures/instrument_rules/btc_usdt_binance_market.json")


@pytest.fixture
def btc_usdt_market() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


class TestMapOhlcvRow:
    def test_maps_all_fields(self) -> None:
        row = [1704067200000, "42000.50", "42500.00", "41900.25", "42300.75", "123.456"]

        bar = map_ohlcv_row("BTC/USDT", "1h", row)

        assert bar.symbol == "BTC/USDT"
        assert bar.timeframe == "1h"
        assert bar.timestamp == datetime(2024, 1, 1, tzinfo=UTC)
        assert bar.open == Decimal("42000.50")
        assert bar.high == Decimal("42500.00")
        assert bar.low == Decimal("41900.25")
        assert bar.close == Decimal("42300.75")
        assert bar.volume == Decimal("123.456")

    def test_accepts_numeric_types_not_just_strings(self) -> None:
        row = [1704067200000, 42000.5, 42500.0, 41900.25, 42300.75, 123.456]

        bar = map_ohlcv_row("BTC/USDT", "1h", row)

        assert bar.open == Decimal("42000.5")

    def test_missing_field_raises_exchange_adapter_error(self) -> None:
        row = [1704067200000, None, "42500.00", "41900.25", "42300.75", "123.456"]

        with pytest.raises(ExchangeAdapterError):
            map_ohlcv_row("BTC/USDT", "1h", row)


class TestMapOhlcvRows:
    def test_maps_multiple_rows_in_order(self) -> None:
        rows = [
            [1704067200000, "100", "110", "90", "105", "1"],
            [1704070800000, "105", "115", "95", "110", "2"],
        ]

        bars = map_ohlcv_rows("BTC/USDT", "1h", rows)

        assert len(bars) == 2
        assert bars[0].timestamp < bars[1].timestamp


class TestMapInstrumentRules:
    def test_maps_btc_usdt_fixture(self, btc_usdt_market: dict) -> None:
        rules = map_instrument_rules("BTC/USDT", btc_usdt_market)

        assert rules.exchange == "binance"
        assert rules.symbol == "BTC/USDT"
        assert rules.tick_size == Decimal("0.01")
        assert rules.step_size == Decimal("0.00001")
        assert rules.min_qty == Decimal("0.00001")
        assert rules.min_notional == Decimal("5.0")
        assert rules.price_precision == 2
        assert rules.qty_precision == 5
        assert rules.maker_fee_rate == Decimal("0.001")
        assert rules.taker_fee_rate == Decimal("0.001")

    def test_falls_back_to_raw_filters_when_cost_limit_missing(self, btc_usdt_market: dict) -> None:
        btc_usdt_market["limits"]["cost"] = {"min": None}

        rules = map_instrument_rules("BTC/USDT", btc_usdt_market)

        assert rules.min_notional == Decimal("5.00000000")

    def test_defaults_min_notional_to_zero_when_undiscoverable(self, btc_usdt_market: dict) -> None:
        btc_usdt_market["limits"]["cost"] = {"min": None}
        btc_usdt_market["info"]["filters"] = []

        rules = map_instrument_rules("BTC/USDT", btc_usdt_market)

        assert rules.min_notional == Decimal("0")

    def test_missing_precision_raises_exchange_adapter_error(self, btc_usdt_market: dict) -> None:
        del btc_usdt_market["precision"]

        with pytest.raises(ExchangeAdapterError):
            map_instrument_rules("BTC/USDT", btc_usdt_market)

    def test_missing_price_precision_raises_exchange_adapter_error(
        self, btc_usdt_market: dict
    ) -> None:
        btc_usdt_market["precision"]["price"] = None

        with pytest.raises(ExchangeAdapterError):
            map_instrument_rules("BTC/USDT", btc_usdt_market)
