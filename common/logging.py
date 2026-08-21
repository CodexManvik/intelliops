"""Structured logging for all services, wired once in create_app.

JSON-lines (one object per line) so any aggregator can parse it; behind
LOG_FORMAT=text|json (default text) so local dev and pytest stay readable.
Every logger.* call across the services picks this up with no code change."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime


class _ServiceFilter(logging.Filter):
    def __init__(self, service_name: str) -> None:
        super().__init__()
        self._service = service_name

    def filter(self, record: logging.LogRecord) -> bool:
        record.service = self._service
        return True


class JsonFormatter(logging.Formatter):
    # stdlib LogRecord attributes we do not want to blindly dump as "extra"
    _RESERVED = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()) | {
        "service",
        "message",
        "asctime",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": getattr(record, "service", "-"),
            "msg": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        # merge caller-supplied extra={...} fields
        for k, v in record.__dict__.items():
            if k not in self._RESERVED and k not in payload:
                payload[k] = v
        return json.dumps(payload, default=str)


def configure_logging(service_name: str, settings) -> None:
    """Install a single root handler + level + service filter. Idempotent."""
    root = logging.getLogger()
    # Clear our own handlers so repeated create_app() calls never stack.
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler()  # stderr
    if getattr(settings, "log_format", "text") == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(service)s] %(message)s")
        )
    handler.addFilter(_ServiceFilter(service_name))
    root.addHandler(handler)
    root.setLevel(getattr(settings, "log_level", "INFO"))
