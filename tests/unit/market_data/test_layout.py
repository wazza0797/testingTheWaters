from __future__ import annotations

from pathlib import Path

from trading_platform.market_data.repository.layout import (
    month_partition_path,
    partition_dir,
    sanitize_symbol,
)


class TestSanitizeSymbol:
    def test_replaces_slash_with_dash(self) -> None:
        assert sanitize_symbol("BTC/USDT") == "BTC-USDT"

    def test_leaves_symbols_without_slash_unchanged(self) -> None:
        assert sanitize_symbol("BTCUSDT") == "BTCUSDT"


class TestPartitionDir:
    def test_builds_expected_directory(self) -> None:
        path = partition_dir(Path("data"), "binance", "BTC/USDT", "1h")
        assert path == Path("data/ohlcv/binance/BTC-USDT/1h")


class TestMonthPartitionPath:
    def test_builds_expected_file_path(self) -> None:
        path = month_partition_path(Path("data"), "binance", "BTC/USDT", "1h", 2024, 3)
        assert path == Path("data/ohlcv/binance/BTC-USDT/1h/2024-03.parquet")

    def test_zero_pads_month(self) -> None:
        path = month_partition_path(Path("data"), "binance", "BTC/USDT", "1h", 2024, 1)
        assert path.name == "2024-01.parquet"
