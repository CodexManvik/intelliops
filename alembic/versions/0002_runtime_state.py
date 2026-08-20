"""runtime state tables: approvals + correlation_baseline"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0002_runtime_state"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("situation_id", sa.String, nullable=False),
        sa.Column("playbook_id", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_approvals_status", "approvals", ["status"])
    op.create_table(
        "correlation_baseline",
        sa.Column("metric_name", sa.String, primary_key=True),
        sa.Column("n", sa.Float, nullable=False),
        sa.Column("mean", sa.Float, nullable=False),
        sa.Column("variance", sa.Float, nullable=False),
        sa.Column("count", sa.BigInteger, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("correlation_baseline")
    op.drop_table("approvals")
