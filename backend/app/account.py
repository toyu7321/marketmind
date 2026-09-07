from __future__ import annotations

import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .database import ActiveSession, BrokerConnection, Invitation, get_db, utcnow
from .schemas import SessionRevokeRequest
from .security import Principal, get_owned_resource, require_authenticated_user, require_sensitive_action_auth, write_audit


router = APIRouter(prefix="/api", tags=["account"])


async def _mapped_invitation(principal: Principal, db: AsyncSession) -> Invitation:
    """Return the one invitation bound to this verified provider identity."""
    if not principal.user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invitation identity could not be verified.")
    provider_email = str(principal.claims.get("email") or "").strip().lower()
    if not provider_email or provider_email != principal.user.email.strip().lower():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invitation identity could not be verified.")
    invitation = (await db.execute(select(Invitation).where(
        Invitation.invited_user_id == principal.user.id,
    ))).scalar_one_or_none()
    if invitation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation onboarding is not available for this account.")
    if (
        invitation.email.strip().lower() != provider_email
        or invitation.role != principal.user.role
        or (invitation.provider_invitation_id and invitation.provider_invitation_id != principal.subject)
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invitation mapping could not be verified.")
    return invitation


@router.get("/account/onboarding")
async def invitation_onboarding(principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    invitation = await _mapped_invitation(principal, db)
    if invitation.status not in {"PENDING", "ACCEPTED"}:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Invitation is no longer available.")
    return {
        "status": invitation.status.lower(),
        "email": principal.user.email,
        "role": principal.user.role,
        "active": principal.user.is_active,
    }


@router.post("/account/onboarding/accept")
async def accept_invitation(request: Request, principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    invitation = await _mapped_invitation(principal, db)
    if invitation.status == "ACCEPTED":
        return {"status": "already_accepted"}
    if invitation.status != "PENDING":
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Invitation is no longer available.")
    now = utcnow()
    invitation.status = "ACCEPTED"
    invitation.accepted_at = now
    principal.user.email_verified = True
    principal.user.last_login_at = now
    await write_audit(db, request, event_type="INVITATION_ACCEPTED", user_id=principal.user.id, resource=f"users/{principal.user.id}")
    await db.commit()
    return {"status": "accepted"}


@router.get("/account")
async def account(principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    connections = (await db.execute(select(BrokerConnection).where(BrokerConnection.user_id == principal.user.id))).scalars().all()
    sessions = (await db.execute(select(ActiveSession).where(ActiveSession.user_id == principal.user.id, ActiveSession.revoked_at.is_(None)))).scalars().all()
    return {
        "user": {"id": principal.user.id, "email": principal.user.email, "display_name": principal.user.display_name, "role": principal.user.role},
        "security": {"mfa_level": principal.aal, "mfa_required_for_admin": get_settings().admin_mfa_required and principal.user.role == "ADMIN", "active_sessions": len(sessions), "user_kill_switch": principal.user.kill_switch_enabled},
        "broker_connection": "Connected" if any(row.status == "ACTIVE" for row in connections) else "Not Connected",
        "paper_trading": "Disabled" if not get_settings().paper_order_submission_enabled else "Preview only",
        "live_trading": "Locked",
    }


@router.get("/account/sessions")
async def account_sessions(principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(ActiveSession).where(ActiveSession.user_id == principal.user.id).order_by(ActiveSession.last_seen_at.desc()))).scalars().all()
    return {"sessions": [{"id": row.id, "current": bool(principal.session_id and row.provider_session_id == principal.session_id), "created_at": row.created_at.isoformat(), "last_seen_at": row.last_seen_at.isoformat(), "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None, "device": row.user_agent[:120] or "Unknown device"} for row in rows]}


@router.post("/account/sessions/revoke")
async def revoke_sessions(payload: SessionRevokeRequest, request: Request, principal: Principal = Depends(require_sensitive_action_auth), db: AsyncSession = Depends(get_db)):
    now = utcnow()
    rows = (await db.execute(select(ActiveSession).where(ActiveSession.user_id == principal.user.id, ActiveSession.revoked_at.is_(None)))).scalars().all()
    changed = 0
    for row in rows:
        if payload.all_other_sessions and row.provider_session_id != principal.session_id:
            row.revoked_at = now
            changed += 1
        elif payload.session_id and row.id == payload.session_id:
            row.revoked_at = now
            changed += 1
    if payload.all_other_sessions:
        # Tokens issued before this instant are rejected unless they carry this
        # explicitly retained session ID. This covers an older, not-yet-seen
        # bearer without logging out the caller's intended current session.
        principal.user.sessions_revoked_at = now
        principal.user.sessions_revocation_exempt_session_id = principal.session_id or None
    await write_audit(db, request, event_type="SESSION_REVOKED", user_id=principal.user.id, resource="account/sessions", safe_metadata={"count": changed, "all_other": payload.all_other_sessions})
    await db.commit()
    return {"status": "revoked", "count": changed}


@router.post("/account/logout")
async def logout_audit(request: Request, principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    if principal.session_id:
        row = (await db.execute(select(ActiveSession).where(ActiveSession.user_id == principal.user.id, ActiveSession.provider_session_id == principal.session_id))).scalar_one_or_none()
        if row:
            row.revoked_at = utcnow()
    await write_audit(db, request, event_type="LOGOUT", user_id=principal.user.id, resource="session")
    await db.commit()
    return {"status": "logged_out"}


@router.get("/broker-connections")
async def broker_connections(principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(BrokerConnection).where(BrokerConnection.user_id == principal.user.id))).scalars().all()
    return {"connections": [{"id": row.id, "provider": row.provider, "environment": row.environment, "status": row.status, "external_account_id": row.external_account_id[-4:] if row.external_account_id else None, "scopes": row.scopes, "updated_at": row.updated_at.isoformat()} for row in rows]}


@router.get("/broker-connections/{connection_id}")
async def broker_connection_detail(connection_id: str, principal: Principal = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    row = await get_owned_resource(db, BrokerConnection, connection_id, principal.user.id)
    return {"id": row.id, "provider": row.provider, "environment": row.environment, "status": row.status, "scopes": row.scopes}


@router.post("/broker-connections/alpaca/authorize")
async def begin_alpaca_authorization(request: Request, principal: Principal = Depends(require_sensitive_action_auth), db: AsyncSession = Depends(get_db)):
    # OAuth is deliberately prepared but not switched on until platform/broker approval is complete.
    settings = get_settings()
    if not getattr(settings, "alpaca_oauth_client_id", "") or not getattr(settings, "alpaca_oauth_redirect_uri", ""):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Alpaca OAuth is not configured for this deployment.")
    state = secrets.token_urlsafe(32)
    state_hash = hashlib.sha256(state.encode()).hexdigest()
    connection = BrokerConnection(user_id=principal.user.id, provider="alpaca", environment="paper", status="OAUTH_PENDING", oauth_state_hash=state_hash)
    db.add(connection)
    await write_audit(db, request, event_type="BROKER_CONNECTION_STARTED", user_id=principal.user.id, resource=f"broker-connections/{connection.id}")
    await db.commit()
    return {"connection_id": connection.id, "state": state, "status": "OAUTH_PENDING", "message": "OAuth callback exchange is intentionally disabled until broker platform approval and secure token storage are configured."}
