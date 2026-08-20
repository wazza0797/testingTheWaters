from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.market_data.instrument_rules_cache import InstrumentRulesCache


class TestInstrumentRulesCache:
    def test_load_returns_none_when_not_cached(self, tmp_path: Path) -> None:
        cache = InstrumentRulesCache(tmp_path)
        assert cache.load("binance", "BTC/USDT") is None

    def test_save_then_load_round_trips_all_fields(
        self, tmp_path: Path, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        cache = InstrumentRulesCache(tmp_path)

        cache.save(btc_usdt_instrument_rules)
        loaded = cache.load("binance", "BTC/USDT")

        assert loaded == btc_usdt_instrument_rules

    def test_writes_to_expected_path(
        self, tmp_path: Path, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        cache = InstrumentRulesCache(tmp_path)

        cache.save(btc_usdt_instrument_rules)

        assert (tmp_path / "instruments" / "binance" / "BTC-USDT.json").exists()

    def test_save_overwrites_existing_cache_entry(
        self, tmp_path: Path, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        cache = InstrumentRulesCache(tmp_path)
        cache.save(btc_usdt_instrument_rules)

        updated = replace(btc_usdt_instrument_rules, maker_fee_rate=Decimal("0.0005"))
        cache.save(updated)

        loaded = cache.load("binance", "BTC/USDT")
        assert loaded == updated
        assert loaded is not None
        assert loaded.maker_fee_rate == Decimal("0.0005")
