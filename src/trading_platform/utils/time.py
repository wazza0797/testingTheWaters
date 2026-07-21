from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_utc(dt: datetime) -> datetime:
    """Normalize a naive or aware datetime to UTC-aware.

    Naive datetimes are assumed to already represent UTC (exchange APIs and
    Parquet-stored bars are always UTC by convention in this codebase).
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
