from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from trading_platform.domain.errors import MarketDataError
from trading_platform.domain.models.bar import Bar
from trading_platform.market_data.repository.layout import month_partition_path, partition_dir

logger = logging.getLogger(__name__)


class ParquetMarketDataRepository:
    """`IMarketDataRepository` backed by Parquet files, partitioned by month:
    `{root}/ohlcv/{exchange}/{symbol}/{timeframe}/YYYY-MM.parquet`.

    Prices/volumes are stored as decimal *strings*, not `float64` columns, so
    round-tripping never loses precision (see coding standards: money/quantities
    are always `Decimal`, never `float`). Writes are merge-by-timestamp, so
    re-downloading an overlapping range is idempotent (no duplicate bars) and
    writes are atomic (write-to-temp + rename) to avoid corrupt files on crash.
    """

    def __init__(self, root: Path, exchange: str = "binance") -> None:
        self._root = root
        self._exchange = exchange

    def save_bars(self, symbol: str, timeframe: str, bars: list[Bar]) -> None:
        if not bars:
            return

        by_month: dict[tuple[int, int], list[Bar]] = defaultdict(list)
        for bar in bars:
            if bar.symbol != symbol or bar.timeframe != timeframe:
                raise MarketDataError(
                    f"Bar symbol/timeframe ({bar.symbol}/{bar.timeframe}) does not match "
                    f"save_bars target ({symbol}/{timeframe})"
                )
            by_month[(bar.timestamp.year, bar.timestamp.month)].append(bar)

        for (year, month), month_bars in sorted(by_month.items()):
            path = month_partition_path(self._root, self._exchange, symbol, timeframe, year, month)
            existing = self._read_bars_file(path, symbol, timeframe) if path.exists() else {}
            for bar in month_bars:
                existing[bar.timestamp] = bar
            ordered = [existing[ts] for ts in sorted(existing)]
            self._write_bars_file(path, ordered)

    def load_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> Iterator[Bar]:
        directory = partition_dir(self._root, self._exchange, symbol, timeframe)
        if not directory.exists():
            return

        bars: list[Bar] = []
        for file_path in sorted(directory.glob("*.parquet")):
            bars.extend(self._read_bars_file(file_path, symbol, timeframe).values())
        bars.sort(key=lambda bar: bar.timestamp)

        for bar in bars:
            if start is not None and bar.timestamp < start:
                continue
            if end is not None and bar.timestamp >= end:
                continue
            yield bar

    def latest_timestamp(self, symbol: str, timeframe: str) -> datetime | None:
        directory = partition_dir(self._root, self._exchange, symbol, timeframe)
        if not directory.exists():
            return None

        files = sorted(directory.glob("*.parquet"))
        if not files:
            return None

        # Filenames sort chronologically (YYYY-MM.parquet), so the last file
        # contains the most recent bars.
        bars = self._read_bars_file(files[-1], symbol, timeframe)
        return max(bars) if bars else None

    def _write_bars_file(self, path: Path, bars: list[Bar]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        table = pa.table(
            {
                "timestamp": [bar.timestamp for bar in bars],
                "open": [str(bar.open) for bar in bars],
                "high": [str(bar.high) for bar in bars],
                "low": [str(bar.low) for bar in bars],
                "close": [str(bar.close) for bar in bars],
                "volume": [str(bar.volume) for bar in bars],
            }
        )
        tmp_path = path.with_name(path.name + ".tmp")
        pq.write_table(table, tmp_path)
        tmp_path.replace(path)

    def _read_bars_file(self, path: Path, symbol: str, timeframe: str) -> dict[datetime, Bar]:
        try:
            table = pq.read_table(path)
        except (pa.ArrowInvalid, OSError) as exc:
            raise MarketDataError(f"Failed to read Parquet file {path}: {exc}") from exc

        result: dict[datetime, Bar] = {}
        for row in table.to_pylist():
            bar = Bar(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=row["timestamp"],
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=Decimal(row["volume"]),
            )
            result[bar.timestamp] = bar
        return result
