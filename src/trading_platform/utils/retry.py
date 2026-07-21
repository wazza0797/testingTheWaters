from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable
from typing import ParamSpec, TypeVar

logger = logging.getLogger(__name__)

P = ParamSpec("P")
T = TypeVar("T")


def retry_with_backoff(
    *,
    max_attempts: int = 3,
    base_delay_seconds: float = 0.5,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Retry a callable with exponential backoff.

    Intended for exchange adapter calls (network flakiness, rate limits) —
    never for domain logic, which must stay deterministic and side-effect free.
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        break
                    delay = base_delay_seconds * (2 ** (attempt - 1))
                    logger.warning(
                        "retrying_after_failure",
                        extra={
                            "function": func.__name__,
                            "attempt": attempt,
                            "delay_seconds": delay,
                            "error": str(exc),
                        },
                    )
                    time.sleep(delay)
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator
