"""author_decisions table: the AI runbook author's memory of its own drafting decisions"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0005_author_decisions"
down_revision = "0004_meridian"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "author_decisions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("signature", sa.String, nullable=False),
        sa.Column("proposal_id", sa.String, nullable=False),
        sa.Column("playbook_id", sa.String, nullable=False),
        sa.Column("disposition", sa.String, nullable=False),
        sa.Column("outcome", sa.String, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
    )
    op.create_index("ix_author_decisions_signature", "author_decisions", ["signature"])
    op.create_index("ix_author_decisions_proposal_id", "author_decisions", ["proposal_id"])
    op.create_index("ix_author_decisions_playbook_id", "author_decisions", ["playbook_id"])


def downgrade() -> None:
    op.drop_table("author_decisions")
