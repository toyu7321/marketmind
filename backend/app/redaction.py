"""Small, dependency-free secret redaction used before persistence or logging."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_SENSITIVE_KEY = re.compile(r"(?:api[_-]?key|secret|token|password|authorization|credential|database[_-]?url|cookie|jwt|private[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|dsn)", re.I)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_ASSIGNMENT = re.compile(r"(?i)\b(api[_-]?key|secret|token|password|authorization|credential|database[_-]?url|cookie|jwt|private[_-]?key)\s*([=:])\s*[^\s,;]+")
_JSON_ASSIGNMENT = re.compile(r'(?i)("(?:api[_-]?key|secret|token|password|authorization|credential|database[_-]?url|cookie|jwt|private[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|dsn)"\s*:\s*")[^"]*')
_URL_CREDENTIALS = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^/@\s:]+:[^/@\s]+@")


def redact_text(value: str) -> str:
    """Remove common credential shapes without trying to parse untrusted logs."""
    value = _BEARER.sub("Bearer [REDACTED]", value)
    value = _URL_CREDENTIALS.sub(r"\1[REDACTED]@", value)
    value = _ASSIGNMENT.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value)
    value = _JSON_ASSIGNMENT.sub(r"\1[REDACTED]", value)
    # Redact sensitive URL query parameters without erasing useful safe route
    # names. Any malformed URL remains protected by the assignment patterns.
    try:
        parsed = urlsplit(value)
        if parsed.scheme and parsed.query:
            query = [(key, "[REDACTED]" if _SENSITIVE_KEY.search(key) else item) for key, item in parse_qsl(parsed.query, keep_blank_values=True)]
            value = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
    except ValueError:
        pass
    return value


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
        # Exception repr/chains frequently reproduce upstream URLs or headers;
        # retain only the structured event and drop the unsafe traceback text.
        if record.exc_info or record.exc_text:
            record.exc_info = None
            record.exc_text = None
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
