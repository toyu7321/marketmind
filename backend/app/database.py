from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def uuid_value() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    auth_subject: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    role: Mapped[str] = mapped_column(String(16), default="USER", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sessions_revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # For an "all other sessions" action, tokens issued before this boundary
    # are rejected unless they belong to this explicitly retained session.
    sessions_revocation_exempt_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    kill_switch_enabled: Mapped[bool] = mapped_column(Boolean, default=False)


class SystemSetting(Base):
    """Administrator-controlled defaults and ceilings, never user-owned."""
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    updated_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)


class UserSetting(Base):
    """Legacy global defaults from the original single-user build. Read-only after migration."""
    __tablename__ = "user_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class UserPreference(Base):
    __tablename__ = "user_preferences"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_user_preferences_user_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(80), index=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class UserRiskProfile(Base):
    __tablename__ = "user_risk_profiles"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    limits: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str | None] = mapped_column(String(36), unique=True, index=True, nullable=True, default=uuid_value)
    # Null represents a retained legacy/global prediction. It is never returned as a private user record.
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    ticker: Mapped[str] = mapped_column(String(12), index=True)
    horizon: Mapped[int]
    bias: Mapped[str] = mapped_column(String(32))
    probabilities: Mapped[dict] = mapped_column(JSON)
    confidence: Mapped[float]
    starting_price: Mapped[float]
    stock_score: Mapped[int]
    market_score: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actual_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    direction_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    invalidation_triggered: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class Portfolio(Base):
    __tablename__ = "portfolios"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_portfolio_user_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80), default="Personal portfolio")
    cash: Mapped[float] = mapped_column(Float, default=100_000.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PortfolioPosition(Base):
    __tablename__ = "portfolio_positions"
    __table_args__ = (UniqueConstraint("portfolio_id", "symbol", name="uq_portfolio_position_symbol"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(12), index=True)
    quantity: Mapped[float] = mapped_column(Float, default=0)
    average_cost: Mapped[float] = mapped_column(Float, default=0)
    sector: Mapped[str] = mapped_column(String(80), default="Unknown")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class SavedStrategy(Base):
    __tablename__ = "saved_strategies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    configuration: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class BrokerConnection(Base):
    __tablename__ = "broker_connections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    environment: Mapped[str] = mapped_column(String(20), default="paper")
    status: Mapped[str] = mapped_column(String(32), default="NOT_CONNECTED")
    external_account_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    scopes: Mapped[dict] = mapped_column(JSON, default=dict)
    # This field is a reference to host-managed encrypted storage, never a plaintext OAuth token.
    encrypted_token_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    oauth_state_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PaperOrder(Base):
    __tablename__ = "paper_orders"
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key", name="uq_paper_order_user_idempotency"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    broker_connection_id: Mapped[str | None] = mapped_column(ForeignKey("broker_connections.id", ondelete="SET NULL"), nullable=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    preview_hash: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="PROPOSED", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TradeIntentRecord(Base):
    """Durable one-shot intent ledger for the future isolated execution layer.

    It contains a normalized signal digest and no broker credential, account
    secret, or opaque provider response. A unique intent ID survives restarts
    and prevents a retried worker from treating a signal as a new order.
    """

    __tablename__ = "trade_intents"
    __table_args__ = (Index("uq_trade_intent_user_payload_hash", "user_id", "payload_hash", unique=True),)

    intent_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    strategy_id: Mapped[str] = mapped_column(String(80), index=True)
    payload_hash: Mapped[str] = mapped_column(String(128))
    broker_client_order_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(32), default="VALIDATED", index=True)
    # The generation is captured at validation. An executor must compare it to
    # the durable control state again when claiming and dispatching work.
    hold_generation: Mapped[int] = mapped_column(Integer, default=0)
    state_version: Mapped[int] = mapped_column(Integer, default=1)
    nonce: Mapped[str] = mapped_column(String(96), default=uuid_value, unique=True, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InstrumentMetadata(Base):
    """Server-verified instrument facts used only by canonical risk controls."""

    __tablename__ = "instrument_metadata"

    symbol: Mapped[str] = mapped_column(String(64), primary_key=True)
    asset_type: Mapped[str] = mapped_column(String(24), index=True)
    underlying_symbol: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    sector: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    theme: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    leverage: Mapped[float] = mapped_column(Float, default=1.0)
    contract_multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    option_strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    option_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    option_right: Mapped[str | None] = mapped_column(String(8), nullable=True)
    source: Mapped[str] = mapped_column(String(48), default="CURATED")
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class PortfolioRiskLedger(Base):
    """Server-written account and loss state; client payloads never populate it."""

    __tablename__ = "portfolio_risk_ledger"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    equity: Mapped[float] = mapped_column(Float, default=0.0)
    peak_equity: Mapped[float] = mapped_column(Float, default=0.0)
    daily_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    weekly_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class StrategyRiskLedger(Base):
    """Registered strategy allocation and loss state, keyed to its owner."""

    __tablename__ = "strategy_risk_ledger"
    __table_args__ = (UniqueConstraint("user_id", "strategy_id", name="uq_strategy_risk_user_strategy"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    strategy_id: Mapped[str] = mapped_column(String(80), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    allocation_value: Mapped[float] = mapped_column(Float, default=0.0)
    daily_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    peak_value: Mapped[float] = mapped_column(Float, default=0.0)
    current_value: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TradingControlState(Base):
    """One durable singleton whose generation invalidates stale intent approval."""

    __tablename__ = "trading_control_state"

    key: Mapped[str] = mapped_column(String(32), primary_key=True, default="global")
    hold_generation: Mapped[int] = mapped_column(Integer, default=0)
    global_hold_active: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TradingHold(Base):
    """Durable, auditable circuit breaker; a release needs controlled action."""

    __tablename__ = "trading_holds"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    scope: Mapped[str] = mapped_column(String(16), index=True)  # GLOBAL, USER, STRATEGY, RECONCILIATION
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    strategy_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    reason_code: Mapped[str] = mapped_column(String(96), index=True)
    hold_generation: Mapped[int] = mapped_column(Integer, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExecutionOutbox(Base):
    """Committed dispatch record. Workers claim it instead of blindly resubmitting."""

    __tablename__ = "execution_outbox"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    intent_id: Mapped[str] = mapped_column(ForeignKey("trade_intents.intent_id", ondelete="CASCADE"), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    broker_client_order_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(128), index=True)
    state: Mapped[str] = mapped_column(String(32), default="VALIDATED", index=True)
    hold_generation: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    dispatch_attempts: Mapped[int] = mapped_column(Integer, default=0)
    broker_status: Mapped[str | None] = mapped_column(String(48), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class BrokerReconciliationState(Base):
    """Mismatch/unknown broker state creates an executor-visible trading hold."""

    __tablename__ = "broker_reconciliation_state"
    __table_args__ = (UniqueConstraint("user_id", "broker_connection_id", name="uq_broker_reconciliation_connection"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    broker_connection_id: Mapped[str | None] = mapped_column(ForeignKey("broker_connections.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="UNVERIFIED", index=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mismatch_reason: Mapped[str | None] = mapped_column(String(180), nullable=True)


class BootstrapState(Base):
    """A singleton row locks one-time remote first-admin bootstrap globally."""

    __tablename__ = "bootstrap_state"

    key: Mapped[str] = mapped_column(String(32), primary_key=True, default="first_admin")
    completed_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, unique=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Invitation(Base):
    __tablename__ = "invitations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(16), default="USER")
    invited_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    invited_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    provider_invitation_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ActiveSession(Base):
    __tablename__ = "active_sessions"
    __table_args__ = (UniqueConstraint("user_id", "provider_session_id", name="uq_active_session_provider"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider_session_id: Mapped[str] = mapped_column(String(128), index=True)
    user_agent: Mapped[str] = mapped_column(String(256), default="")
    ip_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    resource: Mapped[str] = mapped_column(String(180), default="")
    result: Mapped[str] = mapped_column(String(32), default="SUCCESS")
    ip_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_agent: Mapped[str] = mapped_column(String(256), default="")
    safe_metadata: Mapped[dict] = mapped_column(JSON, default=dict)


engine = create_async_engine(get_settings().database_url)
Session = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    # Production schema is migrated by Alembic in the deployment release command.
    if get_settings().should_auto_create_schema:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)


async def get_db():
    async with Session() as db:
        yield db
