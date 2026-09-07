from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hmac
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .database import ActiveSession, AuditEvent, BootstrapState, BrokerConnection, Invitation, SystemSetting, TradingControlState, TradingHold, User, get_db, utcnow
from .schemas import BootstrapAdminRequest, GlobalSecurityUpdate, InviteUserRequest, UserAdminUpdate
from .security import Principal, client_rate_limit_subject, rate_limiter, require_admin, write_audit
from .risk import RISK_POLICY_VERSION, RiskLimits


router = APIRouter(prefix="/api/admin", tags=["admin"])


def user_view(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "is_active": user.is_active,
        "email_verified": user.email_verified,
        "created_at": user.created_at.isoformat(),
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


def _risk_policy_view(values: dict[str, Any] | None) -> dict[str, Any]:
    policy = RiskLimits.from_mapping(values).as_safe_dict()
    policy.pop("policy_version", None)
    return policy


async def _supabase_invite(payload: InviteUserRequest) -> dict[str, Any]:
    settings = get_settings()
    if not settings.auth_ready or not settings.supabase_secret_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Invitation service is not configured.")
    # Supabase admin invitations use an implicit invitation session (PKCE is
    # intentionally unsupported because the accepting browser differs from the
    # administrator's browser). Land directly on the public, client-side
    # consumer so the URL fragment can be exchanged for cookie-backed auth.
    redirect_to = f"{settings.frontend_origin.rstrip('/')}/auth/accept-invite" if settings.frontend_origin else None
    body: dict[str, Any] = {"email": str(payload.email), "data": {"display_name": payload.display_name, "marketmind_role": payload.role}}
    if redirect_to:
        body["redirect_to"] = redirect_to
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(
                f"{settings.supabase_url.rstrip('/')}/auth/v1/invite",
                headers={"apikey": settings.supabase_secret_key, "Authorization": f"Bearer {settings.supabase_secret_key}"},
                json=body,
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Invitation provider could not complete the request.") from error


async def _supabase_user_by_id(auth_subject: str) -> dict[str, Any]:
    """Fetch a user through the server-only Supabase Admin API.

    This deliberately accepts only a UUID validated by the request schema and
    returns no provider credentials to callers or audit metadata.
    """
    settings = get_settings()
    if not settings.auth_ready or not settings.supabase_secret_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Bootstrap identity verification is unavailable.")
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
            response = await client.get(
                f"{settings.supabase_url.rstrip('/')}/auth/v1/admin/users/{auth_subject}",
                headers={"apikey": settings.supabase_secret_key, "Authorization": f"Bearer {settings.supabase_secret_key}"},
            )
    except httpx.HTTPError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Bootstrap identity verification is unavailable.") from error
    if response.status_code == status.HTTP_404_NOT_FOUND:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The supplied identity could not be verified.")
    try:
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Bootstrap identity verification is unavailable.") from error
    user = payload.get("user", payload) if isinstance(payload, dict) else None
    if not isinstance(user, dict) or str(user.get("id", "")) != auth_subject:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The supplied identity could not be verified.")
    return user


async def _bootstrap_rate_limit(request: Request) -> None:
    settings = get_settings()
    await rate_limiter.check(
        "admin-bootstrap",
        client_rate_limit_subject(request),
        limit=settings.bootstrap_admin_rate_limit_per_hour,
        window_seconds=3600,
    )


@router.post("/bootstrap", status_code=status.HTTP_201_CREATED)
async def bootstrap_first_admin(
    payload: BootstrapAdminRequest,
    request: Request,
    response: Response,
    bootstrap_secret: str | None = Header(default=None, alias="X-Bootstrap-Secret"),
    db: AsyncSession = Depends(get_db),
):
    """Create the first admin only during a deliberate, short-lived deployment window."""
    settings = get_settings()
    # A generic not-found response avoids advertising a permanent public route.
    if not settings.bootstrap_admin_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found.")
    await _bootstrap_rate_limit(request)
    if not settings.bootstrap_admin_secret or not bootstrap_secret or not hmac.compare_digest(bootstrap_secret, settings.bootstrap_admin_secret):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bootstrap authorization was not accepted.")

    subject = str(payload.auth_subject)
    provider_user = await _supabase_user_by_id(subject)
    provider_email = str(provider_user.get("email") or "").strip().lower()
    requested_email = str(payload.email).strip().lower()
    if not provider_email or provider_email != requested_email:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The supplied identity and email do not match.")

    # A durable singleton serializes all first-admin contenders. It is not a
    # process lock and remains correct across multiple Render workers/processes.
    state = await db.get(BootstrapState, "first_admin", with_for_update=True)
    if state is None:
        try:
            async with db.begin_nested():
                state = BootstrapState(key="first_admin", expires_at=settings.bootstrap_admin_expires_at or (utcnow() + timedelta(minutes=15)))
                db.add(state)
                await db.flush()
        except IntegrityError:
            state = await db.get(BootstrapState, "first_admin", with_for_update=True)
    if state is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Bootstrap state could not be locked.")
    expires_at = state.expires_at.replace(tzinfo=timezone.utc) if state.expires_at and state.expires_at.tzinfo is None else state.expires_at
    if expires_at and utcnow() >= expires_at and state.completed_user_id is None:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Bootstrap window has expired.")
    if state.completed_user_id:
        existing_admin = await db.get(User, state.completed_user_id)
        if existing_admin and existing_admin.auth_subject == subject and existing_admin.email.lower() == requested_email:
            # Retries from a failed network response are harmless, but never create
            # another account or reopen bootstrap for a different identity.
            await write_audit(db, request, event_type="ADMIN_BOOTSTRAP_REPLAY", user_id=existing_admin.id, resource="admin/bootstrap")
            await db.commit()
            response.status_code = status.HTTP_200_OK
            return {"status": "already_bootstrapped", "user": user_view(existing_admin)}
        await write_audit(db, request, event_type="ADMIN_BOOTSTRAP_REJECTED", resource="admin/bootstrap", result="DENIED", safe_metadata={"reason": "bootstrap_already_completed"})
        await db.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Bootstrap has already been completed.")

    # Defense-in-depth for old databases that already contain an administrator
    # but have not yet materialized the singleton state.
    existing_admin = (await db.execute(select(User).where(User.role == "ADMIN").limit(1).with_for_update())).scalar_one_or_none()
    if existing_admin is not None:
        await write_audit(db, request, event_type="ADMIN_BOOTSTRAP_REJECTED", resource="admin/bootstrap", result="DENIED", safe_metadata={"reason": "administrator_already_exists"})
        await db.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Bootstrap has already been completed.")
    matching_subject = (await db.execute(select(User).where(User.auth_subject == subject))).scalar_one_or_none()
    matching_email = (await db.execute(select(User).where(User.email == requested_email))).scalar_one_or_none()
    if matching_subject and matching_email and matching_subject.id != matching_email.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A local account mapping conflict must be resolved by an administrator.")
    user = matching_subject or matching_email
    if user is None:
        user = User(
            auth_subject=subject,
            email=requested_email,
            display_name=payload.display_name,
            role="ADMIN",
            is_active=True,
            email_verified=bool(provider_user.get("email_confirmed_at")),
        )
        db.add(user)
    else:
        user.auth_subject = subject
        user.email = requested_email
        user.display_name = payload.display_name or user.display_name
        user.role = "ADMIN"
        user.is_active = True
        user.email_verified = bool(provider_user.get("email_confirmed_at"))
    await db.flush()
    state.completed_user_id, state.completed_at = user.id, utcnow()
    await write_audit(db, request, event_type="ADMIN_BOOTSTRAPPED", user_id=user.id, resource="admin/bootstrap")
    await db.commit()
    return {"status": "bootstrapped", "user": user_view(user)}


@router.get("/users")
async def list_users(_: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    users = (await db.execute(select(User).order_by(User.created_at.asc()))).scalars().all()
    return {"users": [user_view(user) for user in users]}


@router.post("/users/invite", status_code=status.HTTP_201_CREATED)
async def invite_user(payload: InviteUserRequest, request: Request, principal: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    existing = (await db.execute(select(User).where(User.email == str(payload.email).lower()))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account or invitation already exists for this email.")
    provider_user = await _supabase_invite(payload)
    subject = str(provider_user.get("id") or provider_user.get("user", {}).get("id") or "")
    if not subject:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Invitation provider returned an invalid response.")
    user = User(auth_subject=subject, email=str(payload.email).lower(), display_name=payload.display_name, role=payload.role, email_verified=False)
    db.add(user)
    await db.flush()
    db.add(Invitation(email=user.email, role=user.role, invited_user_id=user.id, invited_by_user_id=principal.user.id, provider_invitation_id=subject))
    await write_audit(db, request, event_type="USER_INVITED", user_id=principal.user.id, resource=f"users/{user.id}", safe_metadata={"role": user.role})
    await db.commit()
    return {"user": user_view(user), "status": "invited"}


@router.patch("/users/{user_id}")
async def update_user(user_id: str, payload: UserAdminUpdate, request: Request, principal: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    if user.id == principal.user.id and (payload.is_active is False or payload.role == "USER"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Administrators cannot remove their own active administrator access.")
    changed: dict[str, Any] = {}
    if payload.role is not None and payload.role != user.role:
        user.role = payload.role
        user.sessions_revoked_at = utcnow()
        changed["role"] = payload.role
        await write_audit(db, request, event_type="ROLE_CHANGED", user_id=principal.user.id, resource=f"users/{user.id}", safe_metadata={"role": payload.role})
    if payload.is_active is not None and payload.is_active != user.is_active:
        user.is_active = payload.is_active
        user.sessions_revoked_at = utcnow()
        changed["is_active"] = payload.is_active
        await write_audit(db, request, event_type="USER_DISABLED" if not payload.is_active else "USER_REACTIVATED", user_id=principal.user.id, resource=f"users/{user.id}")
    await db.commit()
    return {"user": user_view(user), "changed": changed}


@router.post("/users/{user_id}/revoke-sessions")
async def revoke_user_sessions(user_id: str, request: Request, principal: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    now = utcnow()
    user.sessions_revoked_at = now
    for session in (await db.execute(select(ActiveSession).where(ActiveSession.user_id == user.id, ActiveSession.revoked_at.is_(None)))).scalars():
        session.revoked_at = now
    await write_audit(db, request, event_type="SESSION_REVOKED", user_id=principal.user.id, resource=f"users/{user.id}", safe_metadata={"scope": "all"})
    await db.commit()
    return {"status": "revoked"}


@router.get("/audit")
async def audit_log(limit: int = 100, _: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    limit = max(1, min(limit, 250))
    rows = (await db.execute(select(AuditEvent).order_by(AuditEvent.timestamp.desc()).limit(limit))).scalars().all()
    return {"events": [{
        "event_id": row.event_id, "user_id": row.user_id, "event_type": row.event_type,
        "timestamp": row.timestamp.isoformat(), "resource": row.resource, "result": row.result,
        "safe_metadata": row.safe_metadata,
    } for row in rows]}


@router.get("/security")
async def global_security(_: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = await db.get(SystemSetting, "security")
    raw_policy = (row.value.get("risk_policy") or row.value.get("risk_ceiling") or {}) if row else {}
    policy = _risk_policy_view(raw_policy)
    return {"global_kill_switch": bool(row and row.value.get("global_kill_switch")), "risk_policy": policy, "risk_ceiling": policy, "risk_policy_version": RISK_POLICY_VERSION}


@router.put("/security")
async def update_global_security(payload: GlobalSecurityUpdate, request: Request, principal: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = await db.get(SystemSetting, "security")
    current = dict(row.value) if row else {}
    update = payload.model_dump(exclude_none=True)
    requested_policy = update.pop("risk_policy", None) or update.pop("risk_ceiling", None)
    previous_policy = dict(current.get("risk_policy") or current.get("risk_ceiling") or {})
    if requested_policy is not None:
        # Rebuild through the immutable code-level ceilings before persistence.
        update["risk_policy"] = _risk_policy_view({key: value for key, value in requested_policy.items() if value is not None})
        current.pop("risk_ceiling", None)
    current.update(update)
    if row is None:
        row = SystemSetting(key="security", value=current, updated_by_user_id=principal.user.id)
        db.add(row)
    else:
        row.value, row.updated_by_user_id = current, principal.user.id
    if "global_kill_switch" in update:
        control = await db.get(TradingControlState, "global", with_for_update=True)
        if control is None:
            control = TradingControlState(key="global")
            db.add(control)
            await db.flush()
        control.hold_generation += 1
        control.global_hold_active = bool(update["global_kill_switch"])
        global_holds = (await db.execute(select(TradingHold).where(
            TradingHold.scope == "GLOBAL", TradingHold.reason_code == "GLOBAL_KILL_SWITCH", TradingHold.active.is_(True),
        ))).scalars().all()
        if control.global_hold_active and not global_holds:
            db.add(TradingHold(scope="GLOBAL", reason_code="GLOBAL_KILL_SWITCH", hold_generation=control.hold_generation))
        elif not control.global_hold_active:
            for hold in global_holds:
                hold.active, hold.released_at = False, utcnow()
    if update.get("global_kill_switch") is True:
        event = "GLOBAL_KILL_SWITCH_TRIGGERED"
    elif update.get("global_kill_switch") is False:
        event = "GLOBAL_KILL_SWITCH_RELEASED"
    else:
        event = "GLOBAL_SECURITY_CHANGED"
    await write_audit(db, request, event_type=event, user_id=principal.user.id, resource="system/security", safe_metadata={"global_kill_switch": current.get("global_kill_switch", False), "risk_policy_changed": requested_policy is not None, "risk_policy_previous": previous_policy if requested_policy is not None else None, "risk_policy_new": current.get("risk_policy") if requested_policy is not None else None, "risk_policy_version": RISK_POLICY_VERSION})
    await db.commit()
    policy = _risk_policy_view(current.get("risk_policy"))
    return {"global_kill_switch": bool(current.get("global_kill_switch")), "risk_policy": policy, "risk_ceiling": policy, "risk_policy_version": RISK_POLICY_VERSION}


@router.get("/broker-connections")
async def broker_connection_status(_: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(BrokerConnection).order_by(BrokerConnection.updated_at.desc()))).scalars().all()
    return {"connections": [{"id": row.id, "user_id": row.user_id, "provider": row.provider, "environment": row.environment, "status": row.status, "created_at": row.created_at.isoformat()} for row in rows]}
