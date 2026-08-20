from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from trading_platform.domain.errors import MarketDataError
from trading_platform.domain.models.bar import Bar
from trading_platform.market_data.repository.parquet import ParquetMarketDataRepository


class TestSaveAndLoadRoundTrip:
    def test_round_trips_decimal_values_exactly(
        self, tmp_path: Path, make_bar: Callable[..., Bar]
    ) -> None:
        repo = ParquetMarketDataRepository(tmp_path, exchange="binance")
        bar = make_bar(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            open_="42123.45678901",
            high="42999.99999999",
            low="41000.00000001",
            close="42500.5",
            volume="123.456789",
        )

        repo.save_bars("BTC/USDT", "1h", [bar])
        loaded = list(repo.load_bars("BTC/USDT", "1h"))

        assert len(loaded) == 1
        assert loaded[0].open == Decimal("42123.45678901")
        assert loaded[0].high == Decimal("42999.99999999")
        assert loaded[0].low == Decimal("41000.00000001")
        assert loaded[0].close == Decimal("42500.5")
        assert loaded[0].volume == Decimal("123.456789")
        assert loaded[0].timestamp == bar.timestamp

    def test_load_returns_bars_in_chronological_order(
        self, tmp_path: Path, make_bar: Callable[..., Bar]
    ) -> None:
        repo = ParquetMarketDataRepository(tmp_path)
        bars = [
            make_bar(timestamp=datetime(2024, 1, 1, 2, tzinfo=UTC)),
            make_bar(timestamp=datetime(2024, 1, 1, 0, tzinfo=UTC)),
            make_bar(timestamp=datetime(2024, 1, 1, 1, tzinfo=UTC)),
        ]

        repo.save_bars("BTC/USDT", "1h", bars)
        loaded = list(repo.load_bars("BTC/USDT", "1h"))

        assert [bar.timestamp.hour for bar in loaded] == [0, 1, 2]

    def test_re_saving_overlapping_bars_does_not_duplicate(
        self, tmp_path: Path, make_bar: Callable[..., Bar]
    ) -> None:
        repo = ParquetMarketDataRepository(tmp_path)
        bar1 = make_bar(timestamp=datetime(2024, 1, 1, 0, tzinfo=UTC))
        bar2 = make_bar(timestamp=datetime(2024, 1, 1, 1, tzinfo=UTC))

        repo.save_bars("BTC/USDT", "1h", [bar1, bar2])
        repo.save_bars("BTC/USDT", "1h", [bar2])  # overlapping re-download

        loaded = list(repo.load_bars("BTC/USDT", "1h"))
        assert len(loaded) == 2

    def test_overwriting_a_timestamp_replaces_the_bar(
        self, tmp_path: Path, make_bar: Callable[..., Bar]
    ) -> None:
        repo = ParquetMarketDataRepository(tmp_path)
        ts = datetime(2024, 1, 1, tzinfo=UTC)

        repo.save_bars("BTC/USDT", "1h", [make_bar(timestamp=ts, close="100")])
        repo.save_bars("BTC/USDT", "1h", [make_bar(timestamp=ts, close="108")])

        loaded = list(repo.load_bars("BTC/USDT", "1h"))
        assert len(loaded) == 1
        assert loaded[0].close == Decimal("108")

    def test_bars_spanning_multiple_months_are_partitioned_and_loaded_together(
        self, tmp_path: Path, make_bar: Callable[..., Bar]
    ) -> None:
        repo = ParquetMarketDataRepository(tmp_path)
        bars = [
            make_bar(timestamp=datetime(2024, 1, 15, tzinfo=UTC)),
            make_bar(timestamp=datetime(2024, 2, 15, tzinfo=UTC)),
        ]

        repo.save_bars("BTC/USDT", "1h", bars)

        directory = tmp_path / "ohlcv" / "binance" / "BTC-USDT" / "1h"
        assert sorted(p.name for p in directory.glob("*.parquet")) == [
            "2024-01.parquet",
            "2024-02.parquet",
        ]
        loaded = list(repo.load_bars("BTC/USDT", "1h"))
        assert len(loaded) == 2

    def test_start_end_filters_are_applied(
        self, tmp_path: Path, make_bar: Callable[..., Bar]
    ) -> None:
        repo = ParquetMarketDataRepository(tmp_path)
        bars = [make_bar(timestamp=datetime(2024, 1, 1, hour, tzinfo=UTC)) for hour in range(5)]
        repo.save_bars("BTC/USDT", "1h", bars)

        loaded = list(
            repo.load_bars(
                "BTC/USDT",
                "1h",
                start=datetime(2024, 1, 1, 1, tzinfo=UTC),
                end=datetime(2024, 1, 1, 4, tzinfo=UTC),
            )
        )

        assert [bar.timestamp.hour for bar in loaded] == [1, 2, 3]


class TestLatestTimestamp:
    def test_returns_none_when_no_data(self, tmp_path: Path) -> None:
        repo = ParquetMarketDataRepository(tmp_path)
        assert repo.latest_timestamp("BTC/USDT", "1h") is None

    def test_returns_max_timestamp_across_partitions(
        self, tmp_path: Path, make_bar: Callable[..., Bar]
    ) -> None:
        repo = ParquetMarketDataRepository(tmp_path)
        bars = [
            make_bar(timestamp=datetime(2024, 1, 15, tzinfo=UTC)),
            make_bar(timestamp=datetime(2024, 2, 20, tzinfo=UTC)),
        ]
        repo.save_bars("BTC/USDT", "1h", bars)

        assert repo.latest_timestamp("BTC/USDT", "1h") == datetime(2024, 2, 20, tzinfo=UTC)


class TestSaveBars:
    def test_empty_list_is_a_no_op(self, tmp_path: Path) -> None:
        repo = ParquetMarketDataRepository(tmp_path)
        repo.save_bars("BTC/USDT", "1h", [])
        assert not (tmp_path / "ohlcv").exists()

    def test_mismatched_symbol_raises_market_data_error(
        self, tmp_path: Path, make_bar: Callable[..., Bar]
    ) -> None:
        repo = ParquetMarketDataRepository(tmp_path)
        bar = make_bar(symbol="ETH/USDT")

        with pytest.raises(MarketDataError):
            repo.save_bars("BTC/USDT", "1h", [bar])
