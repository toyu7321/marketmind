import asyncio
from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import (
    AuditEvent,
    BacktestRun,
    BrokerConnection,
    PaperOrder,
    Portfolio,
    PortfolioPosition,
    SavedStrategy,
    Session,
    SystemSetting,
    User,
)
from app.config import Settings
from app.main import app
import app.main as main_module
import app.security as security_module
from app.security import Principal, rate_limiter, require_authenticated_user


async def _create_user(*, role: str = "USER") -> User:
    subject = str(uuid4())
    async with Session() as db:
        user = User(
            auth_subject=subject,
            email=f"{uuid4().hex}@security-test.invalid",
            display_name="Security test user",
            role=role,
            email_verified=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user


async def _owned_resources(user_id: str) -> dict[str, str]:
    async with Session() as db:
        portfolio = Portfolio(user_id=user_id, name=f"Private {uuid4().hex[:8]}")
        backtest = BacktestRun(user_id=user_id, parameters={"ticker": "SPY"}, result={"total_return": 1})
        strategy = SavedStrategy(user_id=user_id, name="Private strategy", configuration={"threshold": 70})
        connection = BrokerConnection(user_id=user_id, provider="alpaca", environment="paper", status="PENDING")
        order = PaperOrder(
            user_id=user_id,
            idempotency_key=uuid4().hex,
            payload={"symbol": "NVDA"},
            preview_hash=uuid4().hex,
            status="REJECTED",
        )
        db.add_all([portfolio, backtest, strategy, connection, order])
        await db.commit()
        return {
            "portfolio": portfolio.id,
            "backtest": backtest.id,
            "strategy": strategy.id,
            "connection": connection.id,
            "order": order.id,
        }


def _principal(user: User, aal: str = "aal2") -> Principal:
    return Principal(
        user=user,
        subject=user.auth_subject,
        email=user.email,
        aal=aal,
        session_id=f"session-{uuid4()}",
        claims={"sub": user.auth_subject, "aal": aal},
    )


@contextmanager
def authenticated_client(principal: Principal):
    app.dependency_overrides[require_authenticated_user] = lambda: principal
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def test_user_owned_settings_and_predictions_cannot_cross_tenant_boundaries():
    owner, other = asyncio.run(_create_user()), asyncio.run(_create_user())
    with authenticated_client(_principal(owner)) as client:
        updated = client.put("/api/settings", json={"watchlist": ["JPM"]})
        assert updated.status_code == 200
        created = client.post("/api/predictions", json={
            "ticker": "NVDA", "horizon": 3, "bias": "Bullish", "bull_probability": 60,
            "neutral_probability": 25, "bear_probability": 15, "confidence": 70,
            "starting_price": 180, "stock_score": 80, "market_score": 72,
        })
        assert created.status_code == 201
        prediction_id = created.json()["id"]
        assert client.get(f"/api/predictions/{prediction_id}").status_code == 200
    with authenticated_client(_principal(other)) as client:
        settings = client.get("/api/settings")
        assert settings.status_code == 200
        assert settings.json()["watchlist"] != ["JPM"]
        assert client.get(f"/api/predictions/{prediction_id}").status_code == 404
        ids = {item["id"] for item in client.get("/api/predictions").json()["predictions"]}
        assert prediction_id not in ids


def test_idor_attempts_return_not_found_for_every_owned_resource_type():
    owner, attacker = asyncio.run(_create_user()), asyncio.run(_create_user())
    resources = asyncio.run(_owned_resources(owner.id))
    paths = [
        f"/api/portfolios/{resources['portfolio']}",
        f"/api/backtests/{resources['backtest']}",
        f"/api/strategies/{resources['strategy']}",
        f"/api/broker-connections/{resources['connection']}",
        f"/api/trading/orders/{resources['order']}",
    ]
    with authenticated_client(_principal(attacker)) as client:
        for path in paths:
            assert client.get(path).status_code == 404


def test_new_user_portfolio_has_a_stable_zero_holdings_contract():
    user = asyncio.run(_create_user(role="ADMIN"))
    with authenticated_client(_principal(user)) as client:
        response = client.get("/api/portfolio")
    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "USER"
    assert payload["positions"] == []
    assert payload["allocation"] == []
    assert payload["orders"] == []
    assert payload["equity_curve"] == []
    assert payload["day_change"] == 0
    assert payload["total_value"] == payload["equity"] == payload["cash"]


def test_personal_portfolios_are_scoped_to_the_authenticated_user():
    owner, other = asyncio.run(_create_user()), asyncio.run(_create_user())

    async def seed_owner_position():
        async with Session() as db:
            portfolio = Portfolio(user_id=owner.id, name="Personal portfolio", cash=10_000)
            db.add(portfolio)
            await db.flush()
            db.add(PortfolioPosition(portfolio_id=portfolio.id, symbol="NVDA", quantity=2, average_cost=100, sector="Technology"))
            await db.commit()

    asyncio.run(seed_owner_position())
    with authenticated_client(_principal(owner)) as client:
        owner_portfolio = client.get("/api/portfolio")
    with authenticated_client(_principal(other)) as client:
        other_portfolio = client.get("/api/portfolio")
    assert owner_portfolio.status_code == other_portfolio.status_code == 200
    assert [position["symbol"] for position in owner_portfolio.json()["positions"]] == ["NVDA"]
    assert other_portfolio.json()["positions"] == []
    assert other_portfolio.json()["allocation"] == []


def test_portfolio_uses_safe_values_when_provider_data_is_partial(monkeypatch):
    user = asyncio.run(_create_user())

    async def seed_position():
        async with Session() as db:
            portfolio = Portfolio(user_id=user.id, name="Personal portfolio", cash=5_000)
            db.add(portfolio)
            await db.flush()
            db.add(PortfolioPosition(portfolio_id=portfolio.id, symbol="MSFT", quantity=3, average_cost=200, sector="Technology"))
            await db.commit()

    class PartialProvider:
        async def get_quote(self, symbol):
            return {}

    asyncio.run(seed_position())
    monkeypatch.setattr(main_module, "market_provider", lambda: PartialProvider())
    with authenticated_client(_principal(user)) as client:
        response = client.get("/api/portfolio")
    assert response.status_code == 200
    position = response.json()["positions"][0]
    assert position["symbol"] == "MSFT"
    assert position["price"] == 200
    assert position["daily_pl"] == 0


def test_admin_endpoints_require_role_and_step_up_authentication():
    regular, admin = asyncio.run(_create_user()), asyncio.run(_create_user(role="ADMIN"))
    with authenticated_client(_principal(regular)) as client:
        assert client.get("/api/admin/users").status_code == 403
    with authenticated_client(_principal(admin, aal="aal1")) as client:
        assert client.get("/api/admin/users").status_code == 403
    with authenticated_client(_principal(admin, aal="aal2")) as client:
        assert client.get("/api/admin/users").status_code == 200


def test_admin_risk_policy_is_mfa_protected_capped_and_audited():
    admin = asyncio.run(_create_user(role="ADMIN"))
    payload = {"risk_policy": {"max_stock_pct": 100, "max_order_notional": 9_999_999, "min_liquidity": 1}}
    with authenticated_client(_principal(admin, aal="aal1")) as client:
        assert client.put("/api/admin/security", json=payload).status_code == 403
    with authenticated_client(_principal(admin, aal="aal2")) as client:
        response = client.put("/api/admin/security", json=payload)
        assert response.status_code == 200
        policy = response.json()["risk_policy"]
        assert policy["max_stock_pct"] == 15
        assert policy["max_order_notional"] == 50_000
        assert policy["min_liquidity"] == 500_000

    async def audit_event():
        async with Session() as db:
            return (await db.execute(select(AuditEvent).where(AuditEvent.user_id == admin.id, AuditEvent.event_type == "GLOBAL_SECURITY_CHANGED"))).scalar_one_or_none()

    event = asyncio.run(audit_event())
    assert event is not None
    assert event.safe_metadata["risk_policy_changed"] is True


def test_sensitive_paper_order_remains_locked_and_audited():
    user = asyncio.run(_create_user())

    async def clear_kill_switch():
        async with Session() as db:
            row = await db.get(SystemSetting, "security")
            if row:
                row.value = {**row.value, "global_kill_switch": False}
            await db.commit()

    asyncio.run(clear_kill_switch())
    order = {
        "symbol": "NVDA", "quantity": 1, "side": "BUY", "order_type": "limit", "limit_price": 100,
        "estimated_price": 100, "confirmed": True,
    }
    with authenticated_client(_principal(user)) as client:
        response = client.post("/api/trading/orders", json=order, headers={"Idempotency-Key": uuid4().hex})
        assert response.status_code == 403
        assert "server policy" in response.json()["detail"]


def test_global_kill_switch_blocks_execution_before_paper_policy():
    user = asyncio.run(_create_user())

    async def enable_kill_switch():
        async with Session() as db:
            row = await db.get(SystemSetting, "security")
            if row:
                row.value = {**row.value, "global_kill_switch": True}
            else:
                db.add(SystemSetting(key="security", value={"global_kill_switch": True}))
            await db.commit()

    asyncio.run(enable_kill_switch())
    order = {
        "symbol": "NVDA", "quantity": 1, "side": "BUY", "order_type": "limit", "limit_price": 100,
        "estimated_price": 100, "confirmed": True,
    }
    with authenticated_client(_principal(user)) as client:
        response = client.post("/api/trading/orders", json=order, headers={"Idempotency-Key": uuid4().hex})
        assert response.status_code == 403
        assert "global safety switch" in response.json()["detail"]


def test_audit_event_is_persisted_without_raw_network_identity():
    user = asyncio.run(_create_user())
    with authenticated_client(_principal(user)) as client:
        assert client.put("/api/settings", json={"refresh_seconds": 90}).status_code == 200

    async def event_exists():
        async with Session() as db:
            statement = select(AuditEvent).where(
                AuditEvent.user_id == user.id,
                AuditEvent.event_type == "SETTINGS_CHANGED",
            )
            return (await db.execute(statement)).scalar_one_or_none()

    event = asyncio.run(event_exists())
    assert event is not None
    assert event.ip_hash is None or len(event.ip_hash) == 64
    assert "127.0.0.1" not in str(event.safe_metadata)


def test_rate_limiter_returns_429_after_its_limit():
    async def exercise():
        scope, subject = f"test-{uuid4()}", str(uuid4())
        await rate_limiter.check(scope, subject, limit=1, window_seconds=60)
        with pytest.raises(HTTPException) as error:
            await rate_limiter.check(scope, subject, limit=1, window_seconds=60)
        assert error.value.status_code == 429

    asyncio.run(exercise())


def test_private_routes_reject_missing_and_invalid_bearer_tokens_when_auth_is_configured(monkeypatch):
    configured = Settings(
        _env_file=None,
        supabase_url="https://project.supabase.co",
        supabase_publishable_key="sb_publishable_test",
    )
    monkeypatch.setattr(security_module, "get_settings", lambda: configured)
    with TestClient(app) as client:
        assert client.get("/api/dashboard").status_code == 401
        assert client.get("/api/dashboard", headers={"Authorization": "Bearer definitely-not-a-jwt"}).status_code == 401
