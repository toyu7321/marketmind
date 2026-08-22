"""Signed, immutable API-to-executor envelope protocol.

The browser never receives this signature and the general FastAPI process
never imports a broker client. The executor must independently revalidate the
canonical facts before it can dispatch anything.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from typing import Any


def canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def sign_envelope(payload: dict[str, Any], key: str) -> str:
    if not key:
        raise ValueError("executor intent signing key is not configured")
    return hmac.new(key.encode("utf-8"), canonical_json(payload), hashlib.sha256).hexdigest()


def verify_envelope(payload: dict[str, Any], signature: str, key: str, *, now: datetime) -> bool:
    if not key or not signature or not hmac.compare_digest(sign_envelope(payload, key), signature):
        return False
    expires_at = payload.get("expires_at")
    if not isinstance(expires_at, str):
        return False
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return expiry.tzinfo is not None and expiry > now
