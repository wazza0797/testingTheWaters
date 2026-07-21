from __future__ import annotations

import json
import logging
import sys
from typing import Any

_STANDARD_LOG_RECORD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {
    "message",
    "asctime",
}


class JsonFormatter(logging.Formatter):
    """Structured JSON log formatter for production/Docker (stdout captured by
    the container runtime). Any `extra=` fields passed to a logging call are
    included alongside the standard timestamp/level/logger/message fields —
    this is how `correlation_id`, `symbol`, `strategy`, and metrics summaries
    surface in logs (see coding standards).
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_RECORD_ATTRS and key not in payload:
                payload[key] = value
        return json.dumps(payload, default=str)


def configure_logging(
    level: str = "INFO",
    fmt: str = "text",
    logger_overrides: dict[str, str] | None = None,
) -> None:
    """Configure the root logger. Call exactly once, at process startup
    (the CLI entrypoint in `main.py`).

    `fmt="json"` is for Docker/production; `fmt="text"` is friendlier for
    local development. `logger_overrides` applies per-logger level overrides
    (see `config/logging.yaml`) on top of the global level.
    """
    root = logging.getLogger()
    root.setLevel(level.upper())

    for existing_handler in list(root.handlers):
        root.removeHandler(existing_handler)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(
        JsonFormatter()
        if fmt == "json"
        else logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    )
    root.addHandler(handler)

    for logger_name, logger_level in (logger_overrides or {}).items():
        logging.getLogger(logger_name).setLevel(logger_level.upper())
