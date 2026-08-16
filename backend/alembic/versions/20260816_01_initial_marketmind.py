"""Initial MarketMind persistence schema."""
from alembic import op
import sqlalchemy as sa

revision = "20260816_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("predictions"):
        op.create_table(
            "predictions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("ticker", sa.String(length=12), nullable=False),
            sa.Column("horizon", sa.Integer(), nullable=False),
            sa.Column("bias", sa.String(length=32), nullable=False),
            sa.Column("probabilities", sa.JSON(), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("starting_price", sa.Float(), nullable=False),
            sa.Column("stock_score", sa.Integer(), nullable=False),
            sa.Column("market_score", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("actual_return", sa.Float(), nullable=True),
            sa.Column("evaluated_at", sa.DateTime(), nullable=True),
            sa.Column("direction_correct", sa.Boolean(), nullable=True),
            sa.Column("invalidation_triggered", sa.Boolean(), nullable=True),
        )
        op.create_index("ix_predictions_ticker", "predictions", ["ticker"])
    else:
        existing = {column["name"] for column in inspector.get_columns("predictions")}
        for name, column in (
            ("evaluated_at", sa.Column("evaluated_at", sa.DateTime(), nullable=True)),
            ("direction_correct", sa.Column("direction_correct", sa.Boolean(), nullable=True)),
            ("invalidation_triggered", sa.Column("invalidation_triggered", sa.Boolean(), nullable=True)),
        ):
            if name not in existing:
                op.add_column("predictions", column)
    if not inspector.has_table("user_settings"):
        op.create_table(
            "user_settings",
            sa.Column("key", sa.String(length=80), primary_key=True),
            sa.Column("value", sa.JSON(), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("user_settings"):
        op.drop_table("user_settings")
    if inspector.has_table("predictions"):
        op.drop_index("ix_predictions_ticker", table_name="predictions")
        op.drop_table("predictions")
