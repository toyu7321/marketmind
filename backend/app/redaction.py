"""Small, dependency-free secret redaction used before persistence or logging."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any


_SENSITIVE_KEY = re.compile(r"(?:api[_-]?key|secret|token|password|authorization|credential|database[_-]?url|cookie|jwt|private[_-]?key)", re.I)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_ASSIGNMENT = re.compile(r"(?i)\b(api[_-]?key|secret|token|password|authorization|credential|database[_-]?url|cookie|jwt|private[_-]?key)\s*([=:])\s*[^\s,;]+")


def redact_text(value: str) -> str:
    """Remove common credential shapes without trying to parse untrusted logs."""
    value = _BEARER.sub("Bearer [REDACTED]", value)
    return _ASSIGNMENT.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value)


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): "[REDACTED]" if _SENSITIVE_KEY.search(str(key)) else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if record.args:
            record.args = redact(record.args)
        for key, value in tuple(record.__dict__.items()):
            if key not in {"msg", "args", "message", "asctime"}:
                record.__dict__[key] = "[REDACTED]" if _SENSITIVE_KEY.search(key) else redact(value)
        return True


def install_secret_redaction() -> None:
    """Install once on root handlers; provider loggers then inherit the filter."""
    root = logging.getLogger()
    if any(isinstance(item, SecretRedactionFilter) for handler in root.handlers for item in handler.filters):
        return
    for handler in root.handlers:
        handler.addFilter(SecretRedactionFilter())
