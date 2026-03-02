"""
Phase 2.1: Structured logging. Configure once at startup; use get_app_logger() for events and errors.
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

# Keys we allow from LogRecord.extra into JSON (no secrets).
_EXTRA_KEYS = frozenset({
    "event", "account_id", "request_id", "path", "error_type",
    "row_count", "duration_ms", "updated", "skipped", "not_found",
    "parse_skipped_total", "topic", "sync_options", "increases", "decreases", "unchanged",
})


class JsonLogFormatter(logging.Formatter):
    """Format each record as a single JSON line (one object per line)."""

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        for key in _EXTRA_KEYS:
            if hasattr(record, key):
                v = getattr(record, key)
                if v is not None:
                    out[key] = v
        return json.dumps(out, default=str)


def configure_logging() -> None:
    """Configure app logging. Call once at startup (e.g. in lifespan)."""
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    log = logging.getLogger("app")
    log.setLevel(level)
    if not log.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(level)
        handler.setFormatter(JsonLogFormatter())
        log.addHandler(handler)
    log.propagate = False


def get_app_logger() -> logging.Logger:
    """Return the app logger for structured events and errors."""
    return logging.getLogger("app")
