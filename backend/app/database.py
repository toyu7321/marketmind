from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
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
