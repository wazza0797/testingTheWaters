from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True, kw_only=True)
class Event:
    """Base class for all typed domain events published on the event bus.

    All events carry a `correlation_id` (traceable across the strategy -> risk ->
    execution chain for a single signal) and a `timestamp`. Subclasses must stay
    frozen dataclasses declared with `kw_only=True` so field ordering across the
    inheritance hierarchy never causes a "non-default argument follows default"
    error.
    """

    correlation_id: str = field(default_factory=new_correlation_id)
    timestamp: datetime = field(default_factory=utc_now)
