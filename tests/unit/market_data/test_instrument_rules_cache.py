from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.market_data.instrument_rules_cache import InstrumentRulesCache
from trading_platform.utils.time import utc_now


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

    def test_freshly_saved_entry_is_not_stale(
        self, tmp_path: Path, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        cache = InstrumentRulesCache(tmp_path, max_age_hours=24.0)
        cache.save(btc_usdt_instrument_rules)

        assert cache.load("binance", "BTC/USDT") is not None

    def test_entry_older_than_max_age_is_treated_as_stale(
        self, tmp_path: Path, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        cache = InstrumentRulesCache(tmp_path, max_age_hours=24.0)
        cache.save(btc_usdt_instrument_rules)

        path = tmp_path / "instruments" / "binance" / "BTC-USDT.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["cached_at"] = (utc_now() - timedelta(hours=25)).isoformat()
        path.write_text(json.dumps(payload), encoding="utf-8")

        assert cache.load("binance", "BTC/USDT") is None

    def test_entry_without_cached_at_field_is_treated_as_stale(
        self, tmp_path: Path, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        """Cache files written before staleness tracking existed shouldn't be
        trusted indefinitely — they're simply treated as missing.
        """
        cache = InstrumentRulesCache(tmp_path)
        cache.save(btc_usdt_instrument_rules)

        path = tmp_path / "instruments" / "binance" / "BTC-USDT.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        del payload["cached_at"]
        path.write_text(json.dumps(payload), encoding="utf-8")

        assert cache.load("binance", "BTC/USDT") is None

    def test_custom_max_age_is_respected(
        self, tmp_path: Path, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        cache = InstrumentRulesCache(tmp_path, max_age_hours=1.0)
        cache.save(btc_usdt_instrument_rules)

        path = tmp_path / "instruments" / "binance" / "BTC-USDT.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["cached_at"] = (utc_now() - timedelta(hours=2)).isoformat()
        path.write_text(json.dumps(payload), encoding="utf-8")

        assert cache.load("binance", "BTC/USDT") is None
