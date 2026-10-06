# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Structured JSON logging with PII redaction.

One JSON object per line, using the field names Cloud Logging parses
automatically (``severity``, ``message``, ``timestamp``). Any ``extra={...}``
fields passed to a log call become top-level JSON fields. Every string value is
passed through ``redact_pii``.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

from app.guardrails import redact_pii

_STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        return redact_pii(value)[0]
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_scrub(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "logger": record.name,
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(_scrub(payload), default=str)


def setup_logging() -> None:
    """Routes the ``enablement.*`` loggers to stderr as JSON (idempotent)."""
    logger = logging.getLogger("enablement")
    if any(getattr(h, "_enablement_json", False) for h in logger.handlers):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    handler._enablement_json = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.setLevel(os.environ.get("ENABLEMENT_LOG_LEVEL", "INFO"))
    logger.propagate = False
