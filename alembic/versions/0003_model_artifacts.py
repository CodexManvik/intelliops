"""model artifacts table: persisted trained-correlator model blobs"""

import sqlalchemy as sa

from alembic import op

revision = "0003_model_artifacts"
down_revision = "0002_runtime_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_artifacts",
        sa.Column("name", sa.String, primary_key=True),
        sa.Column("artifact", sa.LargeBinary, nullable=False),  # -> bytea
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("model_artifacts")
