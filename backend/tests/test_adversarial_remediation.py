"""Permanent regression coverage for the independent execution-risk audit."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.security as security_module
from app.account import logout_audit, revoke_sessions
from app.canonical_risk import build_canonical_risk_context, canonical_decision, persist_circuit_breakers
from app.config import Settings
from app.database import (
    ActiveSession, Base, ExecutionOutbox, InstrumentMetadata, Portfolio, PortfolioPosition,
    PortfolioRiskLedger, SavedStrategy, StrategyRiskLedger, TradeIntentRecord, TradingControlState,
    TradingHold, User,
)
from app.execution_state import claim_outbox, create_validated_intent
from app.reconciliation import record_reconciliation_mismatch
from app.schemas import SessionRevokeRequest
from app.security import Principal, bearer_scheme, require_authenticated_user
from app.redaction import redact, redact_text


class HealthyMarket:
    def __init__(self, *, bid: float = 99.0, ask: float = 100.0, freshness: str = "LIVE") -> None:
        self.bid, self.ask, self.freshness = bid, ask, freshness

    async def get_quote(self, symbol: str):
        return {
            "symbol": symbol, "price": (self.bid + self.ask) / 2, "bid": self.bid, "ask": self.ask,
            "freshness": self.freshness, "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def get_bars(self, symbol: str, days: int = 180):
        return [{"volume": 100_000, "close": (self.bid + self.ask) / 2}]

    async def get_market_status(self):
        return {"is_open": True, "freshness": self.freshness}


async def _database(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'adversarial.db').as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed(session_factory, *, cash: float = 100_000):
    async with session_factory() as db:
        user = User(auth_subject=str(uuid4()), email=f"{uuid4().hex}@test.invalid", email_verified=True)
        db.add(user)
        await db.flush()
        strategy = SavedStrategy(user_id=user.id, name="Authoritative strategy", configuration={})
        db.add(strategy)
        await db.flush()
        db.add_all([
            Portfolio(user_id=user.id, name="Personal portfolio", cash=cash),
            StrategyRiskLedger(user_id=user.id, strategy_id=strategy.id, is_active=True),
        ])
        await db.commit()
        return user.id, strategy.id


async def _portfolio(db, user_id):
    return (await db.execute(select(Portfolio).where(Portfolio.user_id == user_id))).scalar_one()


async def _context(session_factory, user_id, strategy_id, *, symbol="NVDA", quantity=1, side="BUY", provider=None, limit=100):
    async with session_factory() as db:
        user = await db.get(User, user_id)
        return await build_canonical_risk_context(
            db, provider or HealthyMarket(), user=user, symbol=symbol, quantity=quantity, side=side,
            order_type="limit", limit_price=limit, strategy_id=strategy_id,
        )


def test_canonical_post_trade_caps_defeat_incremental_and_client_fact_bypass(tmp_path):
    async def exercise():
        engine, sessions = await _database(tmp_path)
        user_id, strategy_id = await _seed(sessions, cash=86_000)
        async with sessions() as db:
            portfolio = await _portfolio(db, user_id)
            # 14% existing NVDA plus a 2% ticket must reject against an 8% cap.
            db.add(PortfolioPosition(portfolio_id=portfolio.id, symbol="NVDA", quantity=140, average_cost=100, sector="Wrong browser sector"))
            await db.commit()
        context = await _context(sessions, user_id, strategy_id, quantity=20)
        decision = canonical_decision(context, __import__("app.risk", fromlist=["RiskLimits"]).RiskLimits())
        assert decision["decision"] == "REJECTED"
        assert "SYMBOL_POST_TRADE_LIMIT" in decision["reasons"]
        # A malicious client-supplied exposure/price never reached the context:
        # server bid/ask produce the conservative price and aggregate ledger.
        assert context.conservative_price == 100  # authoritative ask, not a client estimate
        await engine.dispose()
    asyncio.run(exercise())


def test_incremental_orders_and_shared_semiconductor_theme_are_aggregate(tmp_path):
    async def exercise():
        engine, sessions = await _database(tmp_path)
        user_id, strategy_id = await _seed(sessions, cash=92_000)
        async with sessions() as db:
            portfolio = await _portfolio(db, user_id)
            db.add_all([
                PortfolioPosition(portfolio_id=portfolio.id, symbol="NVDA", quantity=50, average_cost=100, sector="ignored"),
                PortfolioPosition(portfolio_id=portfolio.id, symbol="AMD", quantity=30, average_cost=100, sector="ignored"),
            ])
            await db.commit()
        # 5% NVDA + 3% AMD already consumes 8% sector/theme; a further 1% NVDA
        # violates both aggregate post-trade concentration checks.
        context = await _context(sessions, user_id, strategy_id, quantity=40)
        decision = canonical_decision(context, __import__("app.risk", fromlist=["RiskLimits"]).RiskLimits())
        assert decision["decision"] == "REJECTED"
        assert {"SYMBOL_POST_TRADE_LIMIT", "SECTOR_POST_TRADE_LIMIT", "THEME_POST_TRADE_LIMIT"} & set(decision["reasons"])
        await engine.dispose()
    asyncio.run(exercise())


def test_option_metadata_overrides_client_hint_and_uses_multiplier_underlying_notional(tmp_path):
    async def exercise():
        engine, sessions = await _database(tmp_path)
        user_id, strategy_id = await _seed(sessions)
        option = "AAPL260918C00200000"
        async with sessions() as db:
            db.add(InstrumentMetadata(symbol=option, asset_type="OPTION", underlying_symbol="AAPL", sector="Technology", theme="MEGA_CAP_TECH", leverage=1, contract_multiplier=100, option_strike=200, option_right="CALL", source="ALPACA"))
            await db.commit()
        context = await _context(sessions, user_id, strategy_id, symbol=option, quantity=1, provider=HealthyMarket(bid=1.9, ask=2.0), limit=2)
        assert context is not None
        assert context.instrument.asset_type == "OPTION"
        assert context.order_notional == 20_000  # $2 premium x 100 is not $2; underlying concentration is larger.
        assert canonical_decision(context, __import__("app.risk", fromlist=["RiskLimits"]).RiskLimits())["decision"] == "REJECTED"
        await engine.dispose()
    asyncio.run(exercise())


def test_unknown_metadata_stale_market_and_rotated_strategy_fail_closed(tmp_path):
    async def exercise():
        engine, sessions = await _database(tmp_path)
        user_id, strategy_id = await _seed(sessions)
        assert await _context(sessions, user_id, strategy_id, symbol="UNKNOWN") is None
        assert await _context(sessions, user_id, strategy_id, provider=HealthyMarket(freshness="STALE")) is None
        assert await _context(sessions, user_id, "rotated-client-strategy") is None
        await engine.dispose()
    asyncio.run(exercise())


def test_loss_breaker_and_reconciliation_are_durable_holds(tmp_path):
    async def exercise():
        engine, sessions = await _database(tmp_path)
        user_id, strategy_id = await _seed(sessions)
        async with sessions() as db:
            db.add(PortfolioRiskLedger(user_id=user_id, equity=100_000, peak_equity=100_000, daily_pnl=-2_500, weekly_pnl=0))
            await db.commit()
        async with sessions() as db:
            user = await db.get(User, user_id)
            context = await build_canonical_risk_context(db, HealthyMarket(), user=user, symbol="NVDA", quantity=1, side="BUY", order_type="limit", limit_price=100, strategy_id=strategy_id)
            decision = canonical_decision(context, __import__("app.risk", fromlist=["RiskLimits"]).RiskLimits())
            assert "DAILY_LOSS_LIMIT" in decision["reasons"]
            created = await persist_circuit_breakers(db, context, decision)
            assert "DAILY_LOSS_LIMIT" in created
            await record_reconciliation_mismatch(db, user_id=user_id, broker_connection_id=None, reason="broker timeout after dispatch")
            await db.commit()
        async with sessions() as db:
            holds = (await db.execute(select(TradingHold).where(TradingHold.user_id == user_id, TradingHold.active.is_(True)))).scalars().all()
            assert {row.reason_code for row in holds} >= {"DAILY_LOSS_LIMIT", "BROKER_RECONCILIATION_MISMATCH"}
        await engine.dispose()
    asyncio.run(exercise())


def test_outbox_deduplicates_uuid_rotation_and_kill_switch_race(tmp_path):
    async def exercise():
        engine, sessions = await _database(tmp_path)
        user_id, strategy_id = await _seed(sessions)
        expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
        payload = {"intent_id": str(uuid4()), "symbol": "NVDA", "side": "BUY", "notional": 1_000, "expires_at": expiry.isoformat()}
        async with sessions() as db:
            first, outbox, replay = await create_validated_intent(db, user_id=user_id, intent_id=payload["intent_id"], strategy_id=strategy_id, expires_at=expiry, canonical_payload=payload)
            assert not replay
            rotated = {**payload, "intent_id": str(uuid4())}
            same, same_outbox, replay = await create_validated_intent(db, user_id=user_id, intent_id=rotated["intent_id"], strategy_id=strategy_id, expires_at=expiry, canonical_payload=rotated)
            assert replay and same.intent_id == first.intent_id and same_outbox.id == outbox.id
            control = await db.get(TradingControlState, "global")
            if control is None:
                control = TradingControlState(key="global")
                db.add(control)
                await db.flush()
            control.hold_generation += 1  # kill switch/hold changed after validation
            db.add(TradingHold(scope="USER", user_id=user_id, reason_code="USER_KILL_SWITCH", hold_generation=control.hold_generation))
            await db.commit()
        async with sessions() as db:
            assert await claim_outbox(db, outbox_id=outbox.id, worker_id="worker-a") is None
            row = await db.get(ExecutionOutbox, outbox.id)
            assert row.state == "REJECTED"
        await engine.dispose()
    asyncio.run(exercise())


def test_revoked_session_bearer_is_rejected_without_logging_out_other_session(tmp_path, monkeypatch):
    async def exercise():
        engine, sessions = await _database(tmp_path)
        user_id, _ = await _seed(sessions)
        configured = Settings(_env_file=None, supabase_url="https://project.supabase.co", supabase_publishable_key="test-key")
        monkeypatch.setattr(security_module, "get_settings", lambda: configured)
        now = int(datetime.now(timezone.utc).timestamp())

        async def claims_for(token, settings):
            session_id = token
            return {"sub": (await _subject(sessions, user_id)), "email": "x@test.invalid", "aal": "aal2", "iat": now, "exp": now + 600, "session_id": session_id}

        async def _request_for(session_id):
            from starlette.requests import Request
            request = Request({"type": "http", "headers": [], "method": "GET", "path": "/api/account", "client": ("127.0.0.1", 1), "scheme": "https"})
            async with sessions() as db:
                principal = await require_authenticated_user(request, type("Cred", (), {"scheme": "Bearer", "credentials": session_id})(), db)
                return principal

        monkeypatch.setattr(security_module.jwks_verifier, "verify", claims_for)
        first, second = await _request_for("session-one"), await _request_for("session-two")
        from starlette.requests import Request
        request = Request({"type": "http", "headers": [], "method": "POST", "path": "/api/account/logout", "client": ("127.0.0.1", 1), "scheme": "https"})
        async with sessions() as db:
            user = await db.get(User, user_id)
            await logout_audit(request, Principal(user, user.auth_subject, user.email, "aal2", "session-one", {}), db)
        with pytest.raises(HTTPException) as rejected:
            await _request_for("session-one")
        assert rejected.value.status_code == 401
        assert (await _request_for("session-two")).session_id == second.session_id
        # Revoke-all-other retains the caller's explicit session exemption but
        # invalidates a previously observed alternate bearer immediately.
        third = await _request_for("session-three")
        request = Request({"type": "http", "headers": [], "method": "POST", "path": "/api/account/sessions/revoke", "client": ("127.0.0.1", 1), "scheme": "https"})
        async with sessions() as db:
            user = await db.get(User, user_id)
            await revoke_sessions(SessionRevokeRequest(all_other_sessions=True), request, Principal(user, user.auth_subject, user.email, "aal2", "session-two", {}), db)
        assert (await _request_for("session-two")).session_id == second.session_id
        with pytest.raises(HTTPException) as other_rejected:
            await _request_for("session-three")
        assert other_rejected.value.status_code == 401
        await engine.dispose()

    async def _subject(session_factory, user_id):
        async with session_factory() as db:
            return (await db.get(User, user_id)).auth_subject

    asyncio.run(exercise())


def test_redaction_handles_nested_arrays_urls_database_credentials_and_json_tokens():
    payload = {
        "rows": [{"refresh_token": "refresh-secret"}, {"url": "https://api.example/path?access_token=abc&safe=yes"}],
        "database_url": "postgresql://alice:password@db.example/marketmind",
    }
    rendered = str(redact(payload)) + redact_text('{"client_secret":"abc","Authorization":"Bearer jwt-value"}')
    assert "refresh-secret" not in rendered
    assert "password@" not in rendered
    assert "jwt-value" not in rendered


def test_remote_paper_execution_flag_is_fail_closed_in_every_environment(monkeypatch):
    monkeypatch.setenv("ENABLE_REMOTE_PAPER_ORDERS", "true")
    with pytest.raises(ValueError, match="ENABLE_REMOTE_PAPER_ORDERS"):
        Settings(_env_file=None)
