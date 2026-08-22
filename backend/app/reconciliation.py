"""Broker reconciliation state; unknown outcomes halt later dispatch attempts."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import BrokerReconciliationState, TradingControlState, TradingHold


async def record_reconciliation_mismatch(
    db: AsyncSession, *, user_id: str, broker_connection_id: str | None, reason: str,
) -> BrokerReconciliationState:
    state = (await db.execute(select(BrokerReconciliationState).where(
        BrokerReconciliationState.user_id == user_id,
        BrokerReconciliationState.broker_connection_id == broker_connection_id,
    ))).scalar_one_or_none()
    if state is None:
        state = BrokerReconciliationState(user_id=user_id, broker_connection_id=broker_connection_id)
        db.add(state)
    state.status, state.mismatch_reason = "MISMATCH", reason[:180]
    control = await db.get(TradingControlState, "global", with_for_update=True)
    if control is None:
        control = TradingControlState(key="global")
        db.add(control)
        await db.flush()
    control.hold_generation += 1
    existing = (await db.execute(select(TradingHold).where(
        TradingHold.active.is_(True), TradingHold.scope == "RECONCILIATION", TradingHold.user_id == user_id,
    ))).scalar_one_or_none()
    if existing is None:
        db.add(TradingHold(scope="RECONCILIATION", user_id=user_id, reason_code="BROKER_RECONCILIATION_MISMATCH", hold_generation=control.hold_generation))
    return state
