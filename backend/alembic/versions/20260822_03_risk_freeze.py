"""Persist one-shot structured trade intents for the execution safety boundary."""

from alembic import op
import sqlalchemy as sa


revision = "20260822_03"
down_revision = "20260816_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("trade_intents"):
        return
    op.create_table(
        "trade_intents",
        sa.Column("intent_id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_id", sa.String(length=80), nullable=False),
        sa.Column("payload_hash", sa.String(length=128), nullable=False),
        sa.Column("broker_client_order_id", sa.String(length=80), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_trade_intents_user_id", "trade_intents", ["user_id"])
    op.create_index("ix_trade_intents_strategy_id", "trade_intents", ["strategy_id"])
    op.create_index("ix_trade_intents_broker_client_order_id", "trade_intents", ["broker_client_order_id"])
    op.create_index("ix_trade_intents_expires_at", "trade_intents", ["expires_at"])
    op.create_index("ix_trade_intents_status", "trade_intents", ["status"])
    op.create_index("ix_trade_intents_created_at", "trade_intents", ["created_at"])


def downgrade() -> None:
    raise RuntimeError("Trade-intent replay history is safety evidence and must be restored from backup, not dropped automatically.")
