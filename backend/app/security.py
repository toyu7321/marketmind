from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar
from uuid import UUID

import httpx
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, get_settings
from .database import ActiveSession, AuditEvent, Base, SystemSetting, User, get_db, utcnow
from .observability import measure


bearer_scheme = HTTPBearer(auto_error=False)
T = TypeVar("T", bound=Base)


@dataclass(slots=True)
class Principal:
    user: User
    subject: str
    email: str
    aal: str
    session_id: str
    claims: dict[str, Any]


class JwksVerifier:
    """Small async JWKS verifier with a bounded cache and strict algorithm allowlist."""

    def __init__(self) -> None:
        self._keys: dict[str, Any] = {}
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def _refresh(self, settings: Settings) -> None:
        async with self._lock:
            if self._keys and time.monotonic() < self._expires_at:
                return
            try:
                async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
                    response = await client.get(settings.jwks_url)
                    response.raise_for_status()
                    document = response.json()
                keys = {entry["kid"]: jwt.PyJWK.from_dict(entry).key for entry in document.get("keys", []) if entry.get("kid")}
            except (httpx.HTTPError, ValueError, jwt.PyJWTError, KeyError) as error:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Authentication verification is temporarily unavailable.") from error
            if not keys:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Authentication verification keys are unavailable.")
            self._keys = keys
            self._expires_at = time.monotonic() + settings.auth_jwks_cache_seconds

    async def verify(self, token: str, settings: Settings) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
            algorithm = header.get("alg")
            key_id = header.get("kid")
        except jwt.PyJWTError as error:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token.") from error
        if algorithm not in settings.jwt_algorithms or not key_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token.")
        await self._refresh(settings)
        key = self._keys.get(key_id)
        if key is None:
            # Key rotation is normal; force one refresh before denying the request.
            self._expires_at = 0.0
            await self._refresh(settings)
            key = self._keys.get(key_id)
        if key is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token.")
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=settings.jwt_algorithms,
                audience=settings.auth_jwt_audience,
                issuer=settings.jwt_issuer,
                options={"require": ["sub", "exp", "iat", "iss", "aud"]},
            )
        except jwt.PyJWTError as error:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired authentication token.") from error
        return claims


jwks_verifier = JwksVerifier()


def _client_ip_hash(request: Request, settings: Settings) -> str | None:
    if not settings.audit_ip_hmac_secret:
        return None
    client_ip = request.client.host if request.client else ""
    if not client_ip:
        return None
    return hmac.new(settings.audit_ip_hmac_secret.encode(), client_ip.encode(), hashlib.sha256).hexdigest()


def _safe_user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "")[:256]


def client_rate_limit_subject(request: Request) -> str:
    """Return a non-reversible in-memory rate-limit subject without storing an IP."""
    settings = get_settings()
    hashed = _client_ip_hash(request, settings)
    if hashed:
        return hashed
    raw = request.client.host if request.client else "unknown"
    return hashlib.sha256(raw.encode()).hexdigest()


async def write_audit(
    db: AsyncSession,
    request: Request,
    *,
    event_type: str,
    user_id: str | None = None,
    resource: str = "",
    result: str = "SUCCESS",
    safe_metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        user_id=user_id,
        event_type=event_type,
        resource=resource[:180],
        result=result[:32],
        ip_hash=_client_ip_hash(request, get_settings()),
        user_agent=_safe_user_agent(request),
        safe_metadata=safe_metadata or {},
    )
    db.add(event)
    return event


async def _record_session(db: AsyncSession, request: Request, principal: Principal) -> None:
    if not principal.session_id:
        return
    row = (await db.execute(select(ActiveSession).where(
        ActiveSession.user_id == principal.user.id,
        ActiveSession.provider_session_id == principal.session_id,
    ))).scalar_one_or_none()
    now = utcnow()
    if row is None:
        db.add(ActiveSession(
            user_id=principal.user.id,
            provider_session_id=principal.session_id,
            user_agent=_safe_user_agent(request),
            ip_hash=_client_ip_hash(request, get_settings()),
        ))
        await write_audit(db, request, event_type="LOGIN_SUCCESS", user_id=principal.user.id, resource="session", safe_metadata={"aal": principal.aal})
    else:
        row.last_seen_at = now


class SlidingWindowRateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, scope: str, subject: str, *, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        key = (scope, subject)
        async with self._lock:
            bucket = self._buckets[key]
            while bucket and bucket[0] <= now - window_seconds:
                bucket.popleft()
            if len(bucket) >= limit:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many requests. Please try again shortly.",
                    headers={"Retry-After": str(window_seconds)},
                )
            bucket.append(now)


rate_limiter = SlidingWindowRateLimiter()


class SessionActivityGate:
    """Throttle non-security-critical session heartbeat writes per process.

    JWT validation, user-active checks, and session-revocation checks still run
    for every request. Only the audit/session "last seen" bookkeeping is
    coalesced for a minute to avoid a write transaction for each panel fetch.
    """

    def __init__(self, interval_seconds: int = 60) -> None:
        self._interval_seconds = interval_seconds
        self._recent: dict[tuple[str, str], float] = {}
        self._lock = asyncio.Lock()

    async def should_touch(self, user_id: str, session_id: str) -> bool:
        now = time.monotonic()
        key = (user_id, session_id or "sessionless")
        async with self._lock:
            if self._recent.get(key, 0) > now:
                return False
            self._recent[key] = now + self._interval_seconds
            if len(self._recent) > 2_000:
                self._recent = {entry: expires for entry, expires in self._recent.items() if expires > now}
            return True


session_activity_gate = SessionActivityGate()


async def get_owned_resource(db: AsyncSession, model: type[T], resource_id: str, user_id: str) -> T:
    resource = await db.get(model, resource_id)
    if resource is None or getattr(resource, "user_id", None) != user_id:
        # 404 avoids confirming another user's resource exists.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found.")
    return resource


async def trading_allowed(db: AsyncSession, principal: Principal) -> None:
    settings = get_settings()
    if settings.enable_live_trading:
        # This is intentionally defensive even though production validation rejects it.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Live trading is locked pending a separately reviewed release.")
    global_security = await db.get(SystemSetting, "security")
    if global_security and bool(global_security.value.get("global_kill_switch")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Trading is disabled by the global safety switch.")
    if principal.user.kill_switch_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Trading is disabled by the user safety switch.")
    if not settings.paper_order_submission_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Paper order submission is disabled by server policy.")


async def require_authenticated_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> Principal:
    with measure("authentication"):
        settings = get_settings()
        if not settings.auth_ready:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Authentication is not configured.")
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication is required.", headers={"WWW-Authenticate": "Bearer"})
        with measure("authentication.jwt"):
            claims = await jwks_verifier.verify(credentials.credentials, settings)
        subject = str(claims.get("sub", ""))
        try:
            UUID(subject)
        except (ValueError, TypeError):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token.")
        with measure("database.auth_user"):
            user = (await db.execute(select(User).where(User.auth_subject == subject))).scalar_one_or_none()
        if user is None or not user.is_active:
            await write_audit(db, request, event_type="LOGIN_FAILED", resource="session", result="DENIED", safe_metadata={"reason": "unknown_or_inactive_user"})
            await db.commit()
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account access is unavailable.")
        issued_at = datetime.fromtimestamp(int(claims["iat"]), tz=timezone.utc)
        revoked_at = user.sessions_revoked_at
        if revoked_at and revoked_at.tzinfo is None:
            revoked_at = revoked_at.replace(tzinfo=timezone.utc)
        if revoked_at and issued_at <= revoked_at:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session has been revoked.")
        principal = Principal(
            user=user,
            subject=subject,
            email=str(claims.get("email") or user.email),
            aal=str(claims.get("aal") or "aal1"),
            session_id=str(claims.get("session_id") or claims.get("sid") or ""),
            claims=claims,
        )
        if await session_activity_gate.should_touch(principal.user.id, principal.session_id):
            user.last_login_at = utcnow()
            with measure("database.auth_session"):
                await _record_session(db, request, principal)
                await db.commit()
        return principal




def rate_limit(scope: str, limit: int, window_seconds: int = 60) -> Callable[..., Any]:
    async def dependency(request: Request, principal: Principal = Depends(require_authenticated_user)) -> None:
        await rate_limiter.check(scope, principal.user.id, limit=limit, window_seconds=window_seconds)
    return dependency


async def require_admin(principal: Principal = Depends(require_authenticated_user)) -> Principal:
    settings = get_settings()
    if principal.user.role != "ADMIN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access is required.")
    if settings.admin_mfa_required and principal.aal != "aal2":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Multi-factor authentication is required for administrator actions.")
    return principal


async def require_sensitive_action_auth(principal: Principal = Depends(require_authenticated_user)) -> Principal:
    """Broker and session-revocation actions require an AAL2 session."""
    if principal.aal != "aal2":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Step-up authentication is required for this sensitive action.")
    return principal
