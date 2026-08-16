import asyncio
from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.admin as admin_module
import app.security as security_module
from app.config import Settings
from app.database import AuditEvent, Base, User
from app.main import app
from app.security import rate_limiter


def bootstrap_settings(*, enabled: bool, limit: int = 5) -> Settings:
    return Settings(
        _env_file=None,
        supabase_url="https://project.supabase.co",
        supabase_publishable_key="sb_publishable_test",
        supabase_secret_key="test-server-secret-not-a-production-credential",
        bootstrap_admin_enabled=enabled,
        bootstrap_admin_secret="bootstrap-test-secret-that-is-at-least-thirty-two-characters",
        bootstrap_admin_rate_limit_per_hour=limit,
    )


@contextmanager
def bootstrap_client(tmp_path, monkeypatch):
    database_path = (tmp_path / "bootstrap.db").as_posix()
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def create_schema():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def test_db():
        async with session_factory() as db:
            yield db

    asyncio.run(create_schema())
    state = {"settings": bootstrap_settings(enabled=True)}

    async def provider_user(subject: str):
        return {
            "id": subject,
            "email": f"{subject[:8]}@example.com",
            "email_confirmed_at": "2026-08-16T00:00:00Z",
        }

    monkeypatch.setattr(admin_module, "get_settings", lambda: state["settings"])
    monkeypatch.setattr(admin_module, "_supabase_user_by_id", provider_user)
    monkeypatch.setattr(security_module, "get_settings", lambda: state["settings"])
    app.dependency_overrides[admin_module.get_db] = test_db
    rate_limiter._buckets.clear()
    try:
        with TestClient(app) as client:
            yield client, state, session_factory
    finally:
        app.dependency_overrides.clear()
        rate_limiter._buckets.clear()
        asyncio.run(engine.dispose())


def payload_for(subject: str) -> dict[str, str]:
    return {
        "auth_subject": subject,
        "email": f"{subject[:8]}@example.com",
        "display_name": "Bootstrap Administrator",
    }


def bootstrap_headers() -> dict[str, str]:
    return {"X-Bootstrap-Secret": "bootstrap-test-secret-that-is-at-least-thirty-two-characters"}


def test_bootstrap_endpoint_is_not_present_when_disabled(tmp_path, monkeypatch):
    with bootstrap_client(tmp_path, monkeypatch) as (client, state, _):
        state["settings"] = bootstrap_settings(enabled=False)
        response = client.post("/api/admin/bootstrap", json=payload_for(str(uuid4())), headers=bootstrap_headers())
        assert response.status_code == 404


def test_bootstrap_rejects_an_incorrect_secret_without_creating_a_user(tmp_path, monkeypatch):
    with bootstrap_client(tmp_path, monkeypatch) as (client, _, session_factory):
        response = client.post("/api/admin/bootstrap", json=payload_for(str(uuid4())), headers={"X-Bootstrap-Secret": "wrong"})
        assert response.status_code == 403

        async def user_count():
            async with session_factory() as db:
                return await db.scalar(select(func.count()).select_from(User))

        assert asyncio.run(user_count()) == 0


def test_bootstrap_creates_verified_first_admin_and_audit_record(tmp_path, monkeypatch):
    subject = str(uuid4())
    with bootstrap_client(tmp_path, monkeypatch) as (client, _, session_factory):
        response = client.post("/api/admin/bootstrap", json=payload_for(subject), headers=bootstrap_headers())
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "bootstrapped"
        assert body["user"]["role"] == "ADMIN"
        assert "bootstrap-test-secret" not in str(body)

        async def stored_records():
            async with session_factory() as db:
                user = (await db.execute(select(User).where(User.auth_subject == subject))).scalar_one()
                audit = (await db.execute(select(AuditEvent).where(AuditEvent.user_id == user.id))).scalar_one()
                return user, audit

        user, audit = asyncio.run(stored_records())
        assert user.email_verified and user.is_active
        assert audit.event_type == "ADMIN_BOOTSTRAPPED"
        assert "secret" not in str(audit.safe_metadata).lower()


def test_exact_bootstrap_retry_is_safe_no_op_but_different_second_attempt_is_rejected(tmp_path, monkeypatch):
    first_subject, second_subject = str(uuid4()), str(uuid4())
    with bootstrap_client(tmp_path, monkeypatch) as (client, _, session_factory):
        assert client.post("/api/admin/bootstrap", json=payload_for(first_subject), headers=bootstrap_headers()).status_code == 201
        replay = client.post("/api/admin/bootstrap", json=payload_for(first_subject), headers=bootstrap_headers())
        assert replay.status_code == 200
        assert replay.json()["status"] == "already_bootstrapped"
        rejected = client.post("/api/admin/bootstrap", json=payload_for(second_subject), headers=bootstrap_headers())
        assert rejected.status_code == 409

        async def user_count():
            async with session_factory() as db:
                return await db.scalar(select(func.count()).select_from(User))

        assert asyncio.run(user_count()) == 1


def test_bootstrap_rate_limit_is_enforced_before_expensive_identity_lookup(tmp_path, monkeypatch):
    with bootstrap_client(tmp_path, monkeypatch) as (client, state, _):
        state["settings"] = bootstrap_settings(enabled=True, limit=1)
        assert client.post("/api/admin/bootstrap", json=payload_for(str(uuid4())), headers={"X-Bootstrap-Secret": "wrong"}).status_code == 403
        assert client.post("/api/admin/bootstrap", json=payload_for(str(uuid4())), headers={"X-Bootstrap-Secret": "wrong"}).status_code == 429
