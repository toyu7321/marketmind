"""Durable intent/outbox state machine with no broker execution dependency."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .database import ExecutionOutbox, PaperOrder, TradeIntentRecord, TradingControlState, TradingHold, utcnow
from .execution_protocol import payload_hash


TERMINAL_STATES = {"REJECTED", "CANCELLED", "FILLED"}
CLAIMABLE_STATES = {"VALIDATED", "RECONCILING"}
ALLOWED_TRANSITIONS = {
    "CREATED": {"VALIDATED", "REJECTED"},
    "VALIDATED": {"CLAIMED", "REJECTED", "CANCELLED", "RECONCILING"},
    "CLAIMED": {"DISPATCHING", "REJECTED", "RECONCILING"},
    "DISPATCHING": {"ACKNOWLEDGED", "UNKNOWN", "RECONCILING"},
    "ACKNOWLEDGED": {"PARTIALLY_FILLED", "FILLED", "CANCELLED", "RECONCILING"},
    "PARTIALLY_FILLED": {"FILLED", "CANCELLED", "RECONCILING"},
    "UNKNOWN": {"RECONCILING"},
    "RECONCILING": {"CLAIMED", "ACKNOWLEDGED", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "REJECTED", "UNKNOWN"},
}


class IntentConflict(Exception):
    pass


async def _control(db: AsyncSession, *, lock: bool = False) -> TradingControlState:
    row = await db.get(TradingControlState, "global", with_for_update=lock)
    if row is None:
        row = TradingControlState(key="global")
        db.add(row)
        await db.flush()
    return row


async def create_validated_intent(
    db: AsyncSession,
    *,
    user_id: str,
    intent_id: str,
    strategy_id: str,
    expires_at: datetime,
    canonical_payload: dict[str, Any],
) -> tuple[TradeIntentRecord, ExecutionOutbox, bool]:
    """Insert exactly once; UUID and payload replay conflicts resolve safely."""
    # Intent UUID is a transport/replay identifier, not business content. A
    # rotated UUID for the same canonical order must be idempotent, not reset a
    # risk budget or create a second dispatch opportunity.
    digest = payload_hash({key: value for key, value in canonical_payload.items() if key != "intent_id"})
    existing = await db.get(TradeIntentRecord, intent_id, with_for_update=True)
    if existing is not None:
        if existing.user_id != user_id or existing.payload_hash != digest:
            raise IntentConflict("intent ID was previously used for different immutable data")
        outbox = (await db.execute(select(ExecutionOutbox).where(ExecutionOutbox.intent_id == existing.intent_id))).scalar_one()
        return existing, outbox, True
    same_payload = (await db.execute(select(TradeIntentRecord).where(TradeIntentRecord.user_id == user_id, TradeIntentRecord.payload_hash == digest).with_for_update())).scalar_one_or_none()
    if same_payload is not None:
        outbox = (await db.execute(select(ExecutionOutbox).where(ExecutionOutbox.intent_id == same_payload.intent_id))).scalar_one()
        return same_payload, outbox, True
    control = await _control(db, lock=True)
    client_order_id = f"mm-{intent_id.replace('-', '')[:24]}"
    record = TradeIntentRecord(
        intent_id=intent_id, user_id=user_id, strategy_id=strategy_id, payload_hash=digest,
        broker_client_order_id=client_order_id, expires_at=expires_at, status="VALIDATED",
        hold_generation=control.hold_generation,
    )
    outbox = ExecutionOutbox(
        intent_id=intent_id, user_id=user_id, broker_client_order_id=client_order_id,
        payload=canonical_payload, payload_hash=digest, state="VALIDATED", hold_generation=control.hold_generation,
    )
    try:
        async with db.begin_nested():
            db.add_all([record, outbox])
            await db.flush()
    except IntegrityError:
        # A competing request committed the immutable record. Re-read and make
        # equal payload retries idempotent; conflict never leaks an IntegrityError.
        existing = await db.get(TradeIntentRecord, intent_id)
        if existing is None or existing.user_id != user_id or existing.payload_hash != digest:
            raise IntentConflict("simultaneous intent request conflicts with an existing immutable record")
        outbox = (await db.execute(select(ExecutionOutbox).where(ExecutionOutbox.intent_id == intent_id))).scalar_one()
        return existing, outbox, True
    return record, outbox, False


async def create_order_receipt(
    db: AsyncSession, *, user_id: str, idempotency_key: str, preview_hash: str, intent_id: str, outbox_id: str,
) -> tuple[PaperOrder, bool]:
    """Race-safe API receipt; equal retries are idempotent, not an IntegrityError."""
    existing = (await db.execute(select(PaperOrder).where(
        PaperOrder.user_id == user_id, PaperOrder.idempotency_key == idempotency_key,
    ).with_for_update())).scalar_one_or_none()
    if existing is not None:
        if existing.preview_hash != preview_hash:
            raise IntentConflict("idempotency key was already used for different canonical data")
        return existing, True
    row = PaperOrder(
        user_id=user_id, idempotency_key=idempotency_key,
        payload={"intent_id": intent_id, "outbox_id": outbox_id}, preview_hash=preview_hash, status="VALIDATED",
    )
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except IntegrityError:
        existing = (await db.execute(select(PaperOrder).where(
            PaperOrder.user_id == user_id, PaperOrder.idempotency_key == idempotency_key,
        ))).scalar_one_or_none()
        if existing is None or existing.preview_hash != preview_hash:
            raise IntentConflict("simultaneous idempotency request conflicts with an existing receipt")
        return existing, True
    return row, False


async def claim_outbox(db: AsyncSession, *, outbox_id: str, worker_id: str, lease_seconds: int = 30) -> ExecutionOutbox | None:
    """Atomically claim a row only while control generation remains unchanged."""
    now = utcnow()
    control = await _control(db, lock=True)
    row = (await db.execute(select(ExecutionOutbox).where(ExecutionOutbox.id == outbox_id).with_for_update(skip_locked=True))).scalar_one_or_none()
    if row is None or row.state not in CLAIMABLE_STATES:
        return None
    active_hold = (await db.execute(select(TradingHold).where(
        TradingHold.active.is_(True), (TradingHold.scope == "GLOBAL") | (TradingHold.user_id == row.user_id),
    ).limit(1))).scalar_one_or_none()
    if active_hold is not None or row.hold_generation != control.hold_generation:
        row.state = "REJECTED"
        return None
    if row.lease_expires_at and row.lease_expires_at > now and row.lease_owner != worker_id:
        return None
    row.state = "CLAIMED"
    row.lease_owner = worker_id[:120]
    row.lease_expires_at = now + timedelta(seconds=max(5, min(lease_seconds, 300)))
    row.dispatch_attempts += 1
    intent = await db.get(TradeIntentRecord, row.intent_id, with_for_update=True)
    if intent is not None:
        intent.status = "CLAIMED"
        intent.lease_owner = row.lease_owner
        intent.lease_expires_at = row.lease_expires_at
        intent.state_version += 1
    return row


async def transition_outbox(db: AsyncSession, *, outbox: ExecutionOutbox, target: str) -> None:
    if target not in ALLOWED_TRANSITIONS.get(outbox.state, set()):
        raise IntentConflict(f"invalid execution state transition {outbox.state}->{target}")
    outbox.state = target
    intent = await db.get(TradeIntentRecord, outbox.intent_id, with_for_update=True)
    if intent is not None:
        intent.status = target
        intent.state_version += 1
        if target == "DISPATCHING":
            intent.dispatched_at = utcnow()
        if target == "ACKNOWLEDGED":
            intent.acknowledged_at = utcnow()
        if target in TERMINAL_STATES:
            intent.consumed_at = utcnow()
