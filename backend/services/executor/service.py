"""Fail-closed executor skeleton; deliberately contains no broker SDK import."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.execution_protocol import verify_envelope


class ExecutionDisabled(RuntimeError):
    pass


@dataclass(slots=True)
class ExecutorService:
    signing_key: str
    worker_id: str

    async def process(self, envelope: dict[str, Any], signature: str) -> None:
        if not verify_envelope(envelope, signature, self.signing_key, now=datetime.now(timezone.utc)):
            raise PermissionError("invalid or expired executor envelope")
        # This boundary intentionally has no execution implementation. Future
        # code must claim/revalidate/reconcile before broker dispatch.
        raise ExecutionDisabled("Executor dispatch is disabled pending re-audit.")
