"""Conservative pacing for IG REST so demo/live stay under allowance buckets.

IG publishes (live defaults; demo is often tighter and can change):

- application / API-key overall
- per-account overall
- per-account trading
- historical data points

We do not scrape live allowances yet — instead we space **all** requests on a
client so a single demo/live session stays under typical demo key budgets
(~20–30/min). Trading and non-trading share one interval because the
api-key allowance counts every call.
"""

from __future__ import annotations

import threading
import time

# Seconds between any two IG REST calls on one client.
# Demo: ~20/min (under common demo api-key caps). Live: ~40/min (under 60/min pub).
DEMO_MIN_INTERVAL_SEC = 3.0
LIVE_MIN_INTERVAL_SEC = 1.5

_ALLOWANCE_MARKERS = (
    "exceeded-api-key-allowance",
    "exceeded-account-allowance",
    "exceeded-account-trading-allowance",
    "exceeded-account-historical-data-allowance",
    "exceeded-public-key-allowance",  # occasional wording variants
)


def is_ig_allowance_error(response_text: str) -> bool:
    lowered = response_text.lower()
    return any(marker in lowered for marker in _ALLOWANCE_MARKERS)


class IgRateLimiter:
    """Thread-safe minimum spacing between IG HTTP calls for one client."""

    def __init__(self, *, min_interval_sec: float) -> None:
        if min_interval_sec < 0:
            raise ValueError("min_interval_sec must be >= 0")
        self._min_interval_sec = min_interval_sec
        self._lock = threading.Lock()
        self._next_allowed_at = 0.0

    @classmethod
    def for_demo(cls) -> IgRateLimiter:
        return cls(min_interval_sec=DEMO_MIN_INTERVAL_SEC)

    @classmethod
    def for_live(cls) -> IgRateLimiter:
        return cls(min_interval_sec=LIVE_MIN_INTERVAL_SEC)

    @property
    def min_interval_sec(self) -> float:
        return self._min_interval_sec

    def wait(self) -> None:
        """Block until the next request is allowed, then reserve that slot."""
        with self._lock:
            now = time.monotonic()
            delay = self._next_allowed_at - now
            if delay > 0:
                time.sleep(delay)
                now = time.monotonic()
            self._next_allowed_at = now + self._min_interval_sec
