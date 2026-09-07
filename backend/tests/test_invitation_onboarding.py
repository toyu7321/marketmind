from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

import app.admin as admin_module
import app.security as security_module
from app.account import accept_invitation, invitation_onboarding
from app.config import Settings
from app.database import AuditEvent, Base, Invitation, User
from app.schemas import InviteUserRequest
from app.security import Principal


async def _database(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'invitation.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_invitation(session_factory, *, status: str = "PENDING"):
    subject = str(uuid4())
    email = f"{uuid4().hex}@example.test"
    async with session_factory() as db:
        inviter = User(auth_subject=str(uuid4()), email=f"{uuid4().hex}@example.test", role="ADMIN", email_verified=True)
        invited = User(auth_subject=subject, email=email, role="USER", email_verified=False, is_active=True)
        db.add_all([inviter, invited])
        await db.flush()
        db.add(Invitation(
            email=email,
            role="USER",
            invited_user_id=invited.id,
            invited_by_user_id=inviter.id,
            provider_invitation_id=subject,
            status=status,
        ))
        await db.commit()
        return invited


def _principal(user: User, *, email: str | None = None) -> Principal:
    provider_email = email if email is not None else user.email
    return Principal(
        user=user,
        subject=user.auth_subject,
        email=provider_email,
        aal="aal1",
        session_id=f"session-{uuid4()}",
        claims={"sub": user.auth_subject, "email": provider_email, "aal": "aal1"},
    )


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/api/account/onboarding/accept", "headers": [], "client": ("127.0.0.1", 1234)})


def test_valid_invitation_is_accepted_without_creating_a_duplicate_user(tmp_path):
    async def exercise():
        engine, sessions = await _database(tmp_path)
        invited = await _seed_invitation(sessions)
        async with sessions() as db:
            mapped = await db.get(User, invited.id)
            pending = await invitation_onboarding(_principal(mapped), db)
            assert pending == {"status": "pending", "email": invited.email, "role": "USER", "active": True}
            result = await accept_invitation(_request(), _principal(mapped), db)
            assert result == {"status": "accepted"}
        async with sessions() as db:
            stored = (await db.execute(select(Invitation).where(Invitation.invited_user_id == invited.id))).scalar_one()
            user = await db.get(User, invited.id)
            assert stored.status == "ACCEPTED" and stored.accepted_at is not None
            assert user.email_verified and user.last_login_at is not None
            assert await db.scalar(select(func.count()).select_from(User)) == 2
            assert await db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.event_type == "INVITATION_ACCEPTED")) == 1
        await engine.dispose()
    asyncio.run(exercise())


def test_reused_invitation_is_an_idempotent_no_op(tmp_path):
    async def exercise():
        engine, sessions = await _database(tmp_path)
        invited = await _seed_invitation(sessions, status="ACCEPTED")
        async with sessions() as db:
            mapped = await db.get(User, invited.id)
            assert await accept_invitation(_request(), _principal(mapped), db) == {"status": "already_accepted"}
            assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 0
        await engine.dispose()
    asyncio.run(exercise())


@pytest.mark.parametrize("failure", ["email", "role", "subject", "inactive"])
def test_invitation_mapping_fails_closed_for_wrong_identity_or_state(tmp_path, failure):
    async def exercise():
        engine, sessions = await _database(tmp_path)
        invited = await _seed_invitation(sessions)
        async with sessions() as db:
            mapped = await db.get(User, invited.id)
            principal = _principal(mapped, email="different@example.test") if failure == "email" else _principal(mapped)
            invitation = (await db.execute(select(Invitation).where(Invitation.invited_user_id == invited.id))).scalar_one()
            if failure == "role": invitation.role = "ADMIN"
            elif failure == "subject": invitation.provider_invitation_id = str(uuid4())
            elif failure == "inactive": mapped.is_active = False
            with pytest.raises(HTTPException) as raised:
                await invitation_onboarding(principal, db)
            assert raised.value.status_code in {403, 409}
        await engine.dispose()
    asyncio.run(exercise())


def test_invite_provider_receives_the_dedicated_acceptance_redirect(monkeypatch):
    captured: dict = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"id": str(uuid4())}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, _url, *, headers, json):
            assert headers["apikey"] == "test-secret"
            captured.update(json)
            return Response()

    monkeypatch.setattr(admin_module, "get_settings", lambda: Settings(
        _env_file=None,
        frontend_origin="https://marketmind.example",
        supabase_url="https://project.supabase.co",
        supabase_publishable_key="publishable-test-key",
        supabase_secret_key="test-secret",
    ))
    monkeypatch.setattr(admin_module.httpx, "AsyncClient", lambda **_: Client())
    asyncio.run(admin_module._supabase_invite(InviteUserRequest(email="invitee@example.com", role="USER")))
    assert captured["redirect_to"] == "https://marketmind.example/auth/accept-invite"


def test_pending_invitation_can_only_access_its_onboarding_api(tmp_path, monkeypatch):
    async def exercise():
        engine, sessions = await _database(tmp_path)
        invited = await _seed_invitation(sessions)
        settings = Settings(
            _env_file=None,
            supabase_url="https://project.supabase.co",
            supabase_publishable_key="publishable-test-key",
        )

        async def verified_claims(_token, _settings):
            return {
                "sub": invited.auth_subject,
                "email": invited.email,
                "aal": "aal1",
                "session_id": f"session-{uuid4()}",
                "iat": 1_788_739_200,
            }

        monkeypatch.setattr(security_module, "get_settings", lambda: settings)
        monkeypatch.setattr(security_module.jwks_verifier, "verify", verified_claims)
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="test-token")
        async with sessions() as db:
            dashboard = Request({"type": "http", "method": "GET", "path": "/api/dashboard", "headers": [], "client": ("127.0.0.1", 1234)})
            with pytest.raises(HTTPException) as denied:
                await security_module.require_authenticated_user(dashboard, credentials, db)
            assert denied.value.status_code == 403
        async with sessions() as db:
            onboarding = Request({"type": "http", "method": "GET", "path": "/api/account/onboarding", "headers": [], "client": ("127.0.0.1", 1234)})
            principal = await security_module.require_authenticated_user(onboarding, credentials, db)
            assert principal.user.id == invited.id
            assert principal.user.last_login_at is None
        await engine.dispose()
    asyncio.run(exercise())
