"""proposed_playbooks table: AI runbook proposals awaiting human approval"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0007_proposed_playbooks"
down_revision = "0006_agent_run_traces"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "proposed_playbooks",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("proposal_id", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("source_situation_id", sa.String, nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
    )
    op.create_index("ix_proposed_playbooks_proposal_id", "proposed_playbooks", ["proposal_id"])
    op.create_index("ix_proposed_playbooks_status", "proposed_playbooks", ["status"])


def downgrade() -> None:
    op.drop_table("proposed_playbooks")
