"""initial store tables"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_records",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("correlation_id", sa.String, nullable=False),
        sa.Column("actor", sa.String, nullable=False),
        sa.Column("action", sa.String, nullable=False),
        sa.Column("resource", sa.String, nullable=False),
        sa.Column("decision", sa.String, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
    )
    op.create_index("ix_audit_correlation_id", "audit_records", ["correlation_id"])
    op.create_index("ix_audit_ts", "audit_records", ["ts"])
    op.create_table(
        "training_records",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("situation_id", sa.String, nullable=False),
        sa.Column("signature", sa.String, nullable=False),
        sa.Column("playbook_id", sa.String, nullable=False),
        sa.Column("result", sa.String, nullable=False),
        sa.Column("worked", sa.Boolean, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
    )
    op.create_index("ix_training_signature", "training_records", ["signature"])
    op.create_table(
        "playbooks",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("hitl_mode", sa.String, nullable=False),
        sa.Column("reversible", sa.Boolean, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("playbooks")
    op.drop_table("training_records")
    op.drop_table("audit_records")
