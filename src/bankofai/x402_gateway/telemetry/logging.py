"""Logging setup for the gateway process."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

_RESERVED_LOG_RECORD_KEYS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}


class JsonFormatter(logging.Formatter):
    """Small JSON formatter for container-friendly operational logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key.startswith("_") or key in _RESERVED_LOG_RECORD_KEYS:
                continue
            payload[key] = _json_safe(value)

        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
    except TypeError:
        return str(value)
    return value


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    **fields: Any,
) -> None:
    extra = {"event": event}
    extra.update(fields)
    logger.log(level, event, extra=extra)


def configure_logging(level: str = "info") -> None:
    normalized = level.upper()
    numeric_level = getattr(logging, normalized, logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=numeric_level, handlers=[handler], force=True)
