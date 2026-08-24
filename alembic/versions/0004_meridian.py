"""meridian tables: sample financial-reporting platform (submissions, reports)"""

import sqlalchemy as sa

from alembic import op

revision = "0004_meridian"
down_revision = "0003_model_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meridian_submissions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("client", sa.String, nullable=False),
        sa.Column("period", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("amount", sa.Float, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "meridian_reports",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("submission_id", sa.BigInteger, nullable=False),
        sa.Column("summary", sa.String, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("meridian_reports")
    op.drop_table("meridian_submissions")
