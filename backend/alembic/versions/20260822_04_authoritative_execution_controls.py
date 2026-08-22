"""Add canonical-risk, durable hold, intent/outbox, and session controls.

This migration is additive: it preserves existing audit and intent evidence and
does not create an executable broker path.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260822_04"
down_revision = "20260822_03"
branch_labels = None
depends_on = None


def _columns(inspector, table: str) -> set[str]:
    return {item["name"] for item in inspector.get_columns(table)} if inspector.has_table(table) else set()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_columns = _columns(inspector, "users")
    if "sessions_revocation_exempt_session_id" not in user_columns:
        op.add_column("users", sa.Column("sessions_revocation_exempt_session_id", sa.String(length=128), nullable=True))

    intent_columns = _columns(inspector, "trade_intents")
    additions = [
        ("hold_generation", sa.Integer(), "0"), ("state_version", sa.Integer(), "1"),
        ("nonce", sa.String(length=96), None), ("lease_owner", sa.String(length=120), None),
        ("lease_expires_at", sa.DateTime(timezone=True), None), ("dispatched_at", sa.DateTime(timezone=True), None),
        ("acknowledged_at", sa.DateTime(timezone=True), None),
    ]
    for name, type_, default in additions:
        if name not in intent_columns:
            op.add_column("trade_intents", sa.Column(name, type_, nullable=True, server_default=sa.text(default) if default else None))
    # Preserve legacy intent evidence while making the new state-machine facts
    # non-null for every row. The existing immutable intent ID is a safe unique
    # nonce for migrated records; new ORM rows generate an independent nonce.
    if inspector.has_table("trade_intents"):
        op.execute("UPDATE trade_intents SET hold_generation = 0 WHERE hold_generation IS NULL")
        op.execute("UPDATE trade_intents SET state_version = 1 WHERE state_version IS NULL")
        op.execute("UPDATE trade_intents SET nonce = intent_id WHERE nonce IS NULL")
        with op.batch_alter_table("trade_intents") as batch:
            batch.alter_column("hold_generation", existing_type=sa.Integer(), nullable=False)
            batch.alter_column("state_version", existing_type=sa.Integer(), nullable=False)
            batch.alter_column("nonce", existing_type=sa.String(length=96), nullable=False)
    existing_indexes = {item["name"] for item in inspector.get_indexes("trade_intents")} if inspector.has_table("trade_intents") else set()
    if "ix_trade_intents_nonce" not in existing_indexes:
        op.create_index("ix_trade_intents_nonce", "trade_intents", ["nonce"], unique=True)
    if "uq_trade_intent_user_payload_hash" not in existing_indexes:
        op.create_index("uq_trade_intent_user_payload_hash", "trade_intents", ["user_id", "payload_hash"], unique=True)
    for name in ("lease_expires_at", "lease_owner"):
        index_name = f"ix_trade_intents_{name}"
        if index_name not in existing_indexes:
            op.create_index(index_name, "trade_intents", [name])

    if not inspector.has_table("instrument_metadata"):
        op.create_table(
            "instrument_metadata",
            sa.Column("symbol", sa.String(length=64), primary_key=True), sa.Column("asset_type", sa.String(length=24), nullable=False),
            sa.Column("underlying_symbol", sa.String(length=24), nullable=True), sa.Column("sector", sa.String(length=80), nullable=True),
            sa.Column("theme", sa.String(length=80), nullable=True), sa.Column("leverage", sa.Float(), nullable=False),
            sa.Column("contract_multiplier", sa.Float(), nullable=False), sa.Column("option_strike", sa.Float(), nullable=True),
            sa.Column("option_expiry", sa.DateTime(timezone=True), nullable=True), sa.Column("option_right", sa.String(length=8), nullable=True),
            sa.Column("source", sa.String(length=48), nullable=False), sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        )
        for name in ("asset_type", "underlying_symbol", "sector", "theme", "verified_at"):
            op.create_index(f"ix_instrument_metadata_{name}", "instrument_metadata", [name])
    if not inspector.has_table("portfolio_risk_ledger"):
        op.create_table(
            "portfolio_risk_ledger", sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("equity", sa.Float(), nullable=False), sa.Column("peak_equity", sa.Float(), nullable=False),
            sa.Column("daily_pnl", sa.Float(), nullable=False), sa.Column("weekly_pnl", sa.Float(), nullable=False),
            sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        )
    if not inspector.has_table("strategy_risk_ledger"):
        op.create_table(
            "strategy_risk_ledger", sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("strategy_id", sa.String(length=80), nullable=False), sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("allocation_value", sa.Float(), nullable=False), sa.Column("daily_pnl", sa.Float(), nullable=False),
            sa.Column("peak_value", sa.Float(), nullable=False), sa.Column("current_value", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("user_id", "strategy_id", name="uq_strategy_risk_user_strategy"),
        )
        op.create_index("ix_strategy_risk_ledger_user_id", "strategy_risk_ledger", ["user_id"])
        op.create_index("ix_strategy_risk_ledger_strategy_id", "strategy_risk_ledger", ["strategy_id"])
        op.create_index("ix_strategy_risk_ledger_is_active", "strategy_risk_ledger", ["is_active"])
    if not inspector.has_table("trading_control_state"):
        op.create_table(
            "trading_control_state", sa.Column("key", sa.String(length=32), primary_key=True),
            sa.Column("hold_generation", sa.Integer(), nullable=False), sa.Column("global_hold_active", sa.Boolean(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    if not inspector.has_table("trading_holds"):
        op.create_table(
            "trading_holds", sa.Column("id", sa.String(length=36), primary_key=True), sa.Column("scope", sa.String(length=16), nullable=False),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
            sa.Column("strategy_id", sa.String(length=80), nullable=True), sa.Column("reason_code", sa.String(length=96), nullable=False),
            sa.Column("hold_generation", sa.Integer(), nullable=False), sa.Column("active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        )
        for name in ("scope", "user_id", "strategy_id", "reason_code", "hold_generation", "active", "created_at"):
            op.create_index(f"ix_trading_holds_{name}", "trading_holds", [name])
    if not inspector.has_table("execution_outbox"):
        op.create_table(
            "execution_outbox", sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("intent_id", sa.String(length=36), sa.ForeignKey("trade_intents.intent_id", ondelete="CASCADE"), nullable=False, unique=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("broker_client_order_id", sa.String(length=80), nullable=False, unique=True), sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("payload_hash", sa.String(length=128), nullable=False), sa.Column("state", sa.String(length=32), nullable=False),
            sa.Column("hold_generation", sa.Integer(), nullable=False), sa.Column("lease_owner", sa.String(length=120), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True), sa.Column("dispatch_attempts", sa.Integer(), nullable=False),
            sa.Column("broker_status", sa.String(length=48), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        for name in ("intent_id", "user_id", "broker_client_order_id", "payload_hash", "state", "hold_generation", "lease_owner", "lease_expires_at", "created_at"):
            op.create_index(f"ix_execution_outbox_{name}", "execution_outbox", [name])
    if not inspector.has_table("broker_reconciliation_state"):
        op.create_table(
            "broker_reconciliation_state", sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("broker_connection_id", sa.String(length=36), sa.ForeignKey("broker_connections.id", ondelete="SET NULL"), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False), sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("mismatch_reason", sa.String(length=180), nullable=True),
            sa.UniqueConstraint("user_id", "broker_connection_id", name="uq_broker_reconciliation_connection"),
        )
        for name in ("user_id", "broker_connection_id", "status"):
            op.create_index(f"ix_broker_reconciliation_state_{name}", "broker_reconciliation_state", [name])
    if not inspector.has_table("bootstrap_state"):
        op.create_table(
            "bootstrap_state", sa.Column("key", sa.String(length=32), primary_key=True),
            sa.Column("completed_user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, unique=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    raise RuntimeError("Risk controls, outbox records, and session evidence must be restored from backup; this migration is intentionally irreversible.")
