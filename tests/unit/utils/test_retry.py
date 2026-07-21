from __future__ import annotations

import pytest

from trading_platform.utils.retry import retry_with_backoff


class TestRetryWithBackoff:
    def test_returns_result_on_first_success(self) -> None:
        calls = {"count": 0}

        @retry_with_backoff(max_attempts=3, base_delay_seconds=0.0)
        def succeeds() -> str:
            calls["count"] += 1
            return "ok"

        assert succeeds() == "ok"
        assert calls["count"] == 1

    def test_retries_then_succeeds(self) -> None:
        calls = {"count": 0}

        @retry_with_backoff(max_attempts=3, base_delay_seconds=0.0)
        def flaky() -> str:
            calls["count"] += 1
            if calls["count"] < 3:
                raise ValueError("transient")
            return "ok"

        assert flaky() == "ok"
        assert calls["count"] == 3

    def test_raises_last_exception_after_exhausting_attempts(self) -> None:
        calls = {"count": 0}

        @retry_with_backoff(max_attempts=2, base_delay_seconds=0.0)
        def always_fails() -> None:
            calls["count"] += 1
            raise ValueError(f"failure {calls['count']}")

        with pytest.raises(ValueError, match="failure 2"):
            always_fails()
        assert calls["count"] == 2

    def test_only_configured_exceptions_are_retried(self) -> None:
        @retry_with_backoff(max_attempts=3, base_delay_seconds=0.0, exceptions=(ValueError,))
        def raises_type_error() -> None:
            raise TypeError("not retried")

        with pytest.raises(TypeError):
            raises_type_error()
