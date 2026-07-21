from __future__ import annotations

import json
import logging

from trading_platform.utils.logging import JsonFormatter, configure_logging


class TestConfigureLogging:
    def test_sets_root_logger_level(self) -> None:
        configure_logging(level="DEBUG", fmt="text")
        assert logging.getLogger().level == logging.DEBUG

    def test_replaces_existing_handlers_instead_of_stacking(self) -> None:
        configure_logging(level="INFO", fmt="text")
        configure_logging(level="INFO", fmt="text")
        assert len(logging.getLogger().handlers) == 1

    def test_logger_overrides_apply_specific_levels(self) -> None:
        configure_logging(level="INFO", fmt="text", logger_overrides={"uvicorn.access": "WARNING"})
        assert logging.getLogger("uvicorn.access").level == logging.WARNING


class TestJsonFormatter:
    def test_format_produces_valid_json_with_standard_fields(self) -> None:
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        formatted = JsonFormatter().format(record)
        payload = json.loads(formatted)

        assert payload["message"] == "hello"
        assert payload["level"] == "INFO"
        assert payload["logger"] == "test.logger"

    def test_format_includes_extra_fields(self) -> None:
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="fill_received",
            args=(),
            exc_info=None,
        )
        record.correlation_id = "abc-123"
        record.symbol = "BTC/USDT"

        payload = json.loads(JsonFormatter().format(record))

        assert payload["correlation_id"] == "abc-123"
        assert payload["symbol"] == "BTC/USDT"
