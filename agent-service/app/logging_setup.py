"""Structured JSON logging to stdout.

Pass structured fields via `logger.info("event_name", extra={"extra_fields": {...}})`.
This gives us a machine-readable audit trail from day one — the same shape the
`create_ticket` PreToolUse hook will emit in later phases.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Quiet noisy third-party loggers; keep our own at the configured level.
    for noisy in ("slack_bolt", "slack_sdk", "aiohttp", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
