"""Live Binance smoke tests — excluded by default (see `pytest.ini_options.addopts`
convention: run explicitly with `pytest -m network`). Requires internet access.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from trading_platform.exchanges.binance.adapter import BinanceAdapter

pytestmark = pytest.mark.network


class TestBinanceAdapterLive:
    def test_fetch_ohlcv_returns_recent_bars(self) -> None:
        adapter = BinanceAdapter()
        since = datetime.now(UTC) - timedelta(hours=5)

        bars = adapter.fetch_ohlcv("BTC/USDT", "1h", since=since, limit=5)

        assert len(bars) > 0
        assert all(bar.symbol == "BTC/USDT" for bar in bars)

    def test_fetch_instrument_rules_returns_sane_btc_usdt_rules(self) -> None:
        adapter = BinanceAdapter()

        rules = adapter.fetch_instrument_rules("BTC/USDT")

        assert rules.exchange == "binance"
        assert rules.tick_size > 0
        assert rules.step_size > 0
        assert rules.min_notional > 0
