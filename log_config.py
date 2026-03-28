"""Logging configuration for Hyperliquid trading bot.

Supports two output formats controlled by the ``LOG_FORMAT`` env var:

- ``text`` (default): Human-readable format for development / terminal use.
- ``json``: Structured JSON, one object per line — suitable for log
  aggregation tools (Datadog, CloudWatch, Loki, etc.).

Usage
-----
Call ``setup_logging()`` once at startup (before any ``getLogger`` calls
produce output).  All existing loggers that use the root handler will
automatically pick up the configured formatter.
"""

import json
import logging
import os
import traceback
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Include exception info when present
        if record.exc_info and record.exc_info[0] is not None:
            entry["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": traceback.format_exception(*record.exc_info),
            }

        # Propagate extra fields attached via `logger.info("msg", extra={...})`
        for key in ("coin", "action", "side", "size", "price", "order_id", "strategy", "dex"):
            if hasattr(record, key):
                entry[key] = getattr(record, key)

        return json.dumps(entry, default=str)


TEXT_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


def setup_logging() -> None:
    """Configure the root logger based on ``LOG_FORMAT`` and ``LOG_LEVEL`` env vars."""
    log_format = os.getenv("LOG_FORMAT", "text").lower().strip()
    log_level = os.getenv("LOG_LEVEL", "INFO").upper().strip()

    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level, logging.INFO))

    # Remove any pre-existing handlers (e.g. from basicConfig)
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler()

    if log_format == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter(TEXT_FORMAT))

    root.addHandler(handler)
