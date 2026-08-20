from __future__ import annotations

from pathlib import Path


def sanitize_symbol(symbol: str) -> str:
    """Filesystem-safe form of a symbol, e.g. `'BTC/USDT'` -> `'BTC-USDT'`."""
    return symbol.replace("/", "-")


def partition_dir(root: Path, exchange: str, symbol: str, timeframe: str) -> Path:
    """Directory holding one Parquet file per month for a given symbol/timeframe:
    `{root}/ohlcv/{exchange}/{symbol}/{timeframe}/`.
    """
    return root / "ohlcv" / exchange / sanitize_symbol(symbol) / timeframe


def month_partition_path(
    root: Path, exchange: str, symbol: str, timeframe: str, year: int, month: int
) -> Path:
    """Path to the Parquet file for one calendar month, e.g. `.../2024-03.parquet`."""
    return partition_dir(root, exchange, symbol, timeframe) / f"{year:04d}-{month:02d}.parquet"
