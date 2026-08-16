"""Add fail-closed multi-user ownership and security persistence.

Existing `user_settings.runtime_settings` remains a global legacy default and old
predictions remain ownerless archival records. No existing record is deleted.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260816_02"
down_revision = "20260816_01"
branch_labels = None
depends_on = None


def _has_table(inspector, name: str) -> bool:
    return inspector.has_table(name)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, "users"):
        op.create_table(
            "users",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("auth_subject", sa.String(length=64), nullable=False, unique=True),
            sa.Column("email", sa.String(length=320), nullable=False, unique=True),
            sa.Column("display_name", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("role", sa.String(length=16), nullable=False, server_default="USER"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("sessions_revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("kill_switch_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
        op.create_index("ix_users_auth_subject", "users", ["auth_subject"])
        op.create_index("ix_users_email", "users", ["email"])
        op.create_index("ix_users_role", "users", ["role"])
        op.create_index("ix_users_is_active", "users", ["is_active"])

    if not _has_table(inspector, "system_settings"):
        op.create_table(
            "system_settings",
            sa.Column("key", sa.String(length=80), primary_key=True),
            sa.Column("value", sa.JSON(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_by_user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True),
        )
        op.create_index("ix_system_settings_updated_by_user_id", "system_settings", ["updated_by_user_id"])

    if not _has_table(inspector, "user_preferences"):
        op.create_table(
            "user_preferences",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("key", sa.String(length=80), nullable=False),
            sa.Column("value", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("user_id", "key", name="uq_user_preferences_user_key"),
        )
        op.create_index("ix_user_preferences_user_id", "user_preferences", ["user_id"])
        op.create_index("ix_user_preferences_key", "user_preferences", ["key"])

    if not _has_table(inspector, "user_risk_profiles"):
        op.create_table(
            "user_risk_profiles",
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("limits", sa.JSON(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )

    if _has_table(inspector, "predictions"):
        columns = {column["name"] for column in inspector.get_columns("predictions")}
        if "public_id" not in columns:
            op.add_column("predictions", sa.Column("public_id", sa.String(length=36), nullable=True))
            op.create_index("ix_predictions_public_id", "predictions", ["public_id"], unique=True)
        if "user_id" not in columns:
            # SQLite cannot add a foreign-key constraint with ALTER TABLE. Existing
            # legacy predictions remain ownerless; PostgreSQL receives the FK.
            user_id_column = sa.Column("user_id", sa.String(length=36), nullable=True)
            if bind.dialect.name != "sqlite":
                user_id_column = sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
            op.add_column("predictions", user_id_column)
            op.create_index("ix_predictions_user_id", "predictions", ["user_id"])
        existing_indexes = {index["name"] for index in inspector.get_indexes("predictions")}
        if "created_at" in columns and "ix_predictions_created_at" not in existing_indexes:
            op.create_index("ix_predictions_created_at", "predictions", ["created_at"])

    if not _has_table(inspector, "portfolios"):
        op.create_table(
            "portfolios",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(length=80), nullable=False),
            sa.Column("cash", sa.Float(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("user_id", "name", name="uq_portfolio_user_name"),
        )
        op.create_index("ix_portfolios_user_id", "portfolios", ["user_id"])

    if not _has_table(inspector, "portfolio_positions"):
        op.create_table(
            "portfolio_positions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("portfolio_id", sa.String(length=36), sa.ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False),
            sa.Column("symbol", sa.String(length=12), nullable=False),
            sa.Column("quantity", sa.Float(), nullable=False),
            sa.Column("average_cost", sa.Float(), nullable=False),
            sa.Column("sector", sa.String(length=80), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("portfolio_id", "symbol", name="uq_portfolio_position_symbol"),
        )
        op.create_index("ix_portfolio_positions_portfolio_id", "portfolio_positions", ["portfolio_id"])
        op.create_index("ix_portfolio_positions_symbol", "portfolio_positions", ["symbol"])

    if not _has_table(inspector, "backtest_runs"):
        op.create_table(
            "backtest_runs",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("parameters", sa.JSON(), nullable=False),
            sa.Column("result", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_backtest_runs_user_id", "backtest_runs", ["user_id"])
        op.create_index("ix_backtest_runs_created_at", "backtest_runs", ["created_at"])

    if not _has_table(inspector, "saved_strategies"):
        op.create_table(
            "saved_strategies",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("configuration", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_saved_strategies_user_id", "saved_strategies", ["user_id"])

    if not _has_table(inspector, "broker_connections"):
        op.create_table(
            "broker_connections",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("provider", sa.String(length=40), nullable=False),
            sa.Column("environment", sa.String(length=20), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("external_account_id", sa.String(length=160), nullable=True),
            sa.Column("scopes", sa.JSON(), nullable=False),
            sa.Column("encrypted_token_reference", sa.String(length=255), nullable=True),
            sa.Column("oauth_state_hash", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_broker_connections_user_id", "broker_connections", ["user_id"])
        op.create_index("ix_broker_connections_provider", "broker_connections", ["provider"])

    if not _has_table(inspector, "paper_orders"):
        op.create_table(
            "paper_orders",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("broker_connection_id", sa.String(length=36), sa.ForeignKey("broker_connections.id", ondelete="SET NULL"), nullable=True),
            sa.Column("idempotency_key", sa.String(length=128), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("preview_hash", sa.String(length=128), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("user_id", "idempotency_key", name="uq_paper_order_user_idempotency"),
        )
        op.create_index("ix_paper_orders_user_id", "paper_orders", ["user_id"])
        op.create_index("ix_paper_orders_broker_connection_id", "paper_orders", ["broker_connection_id"])
        op.create_index("ix_paper_orders_created_at", "paper_orders", ["created_at"])

    if not _has_table(inspector, "invitations"):
        op.create_table(
            "invitations",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("email", sa.String(length=320), nullable=False, unique=True),
            sa.Column("role", sa.String(length=16), nullable=False),
            sa.Column("invited_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("invited_by_user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("provider_invitation_id", sa.String(length=80), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_invitations_email", "invitations", ["email"])
        op.create_index("ix_invitations_invited_user_id", "invitations", ["invited_user_id"])
        op.create_index("ix_invitations_invited_by_user_id", "invitations", ["invited_by_user_id"])

    if not _has_table(inspector, "active_sessions"):
        op.create_table(
            "active_sessions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("provider_session_id", sa.String(length=128), nullable=False),
            sa.Column("user_agent", sa.String(length=256), nullable=False),
            sa.Column("ip_hash", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("user_id", "provider_session_id", name="uq_active_session_provider"),
        )
        op.create_index("ix_active_sessions_user_id", "active_sessions", ["user_id"])
        op.create_index("ix_active_sessions_provider_session_id", "active_sessions", ["provider_session_id"])
        op.create_index("ix_active_sessions_last_seen_at", "active_sessions", ["last_seen_at"])

    if not _has_table(inspector, "audit_events"):
        op.create_table(
            "audit_events",
            sa.Column("event_id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
            sa.Column("resource", sa.String(length=180), nullable=False),
            sa.Column("result", sa.String(length=32), nullable=False),
            sa.Column("ip_hash", sa.String(length=128), nullable=True),
            sa.Column("user_agent", sa.String(length=256), nullable=False),
            sa.Column("safe_metadata", sa.JSON(), nullable=False),
        )
        op.create_index("ix_audit_events_user_id", "audit_events", ["user_id"])
        op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])
        op.create_index("ix_audit_events_timestamp", "audit_events", ["timestamp"])


def downgrade() -> None:
    # Security audit history and user-owned records must not be automatically destroyed.
    raise RuntimeError("This security migration is intentionally irreversible; restore from backup for a rollback.")
