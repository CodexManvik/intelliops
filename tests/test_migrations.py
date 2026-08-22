import pytest
from sqlalchemy import text


@pytest.mark.postgres
def test_alembic_upgrade_creates_schema():
    import os

    from alembic.config import Config
    from testcontainers.postgres import PostgresContainer

    from alembic import command


    with PostgresContainer("postgres:16-alpine") as pg:
        url = pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql+psycopg://")
        os.environ["INTELLIOPS_DATABASE_URL"] = url
        cfg = Config("alembic.ini")
        command.upgrade(cfg, "head")
        from common.db import make_engine


        with make_engine(url).connect() as conn:
            tables = set(
                conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
                    )
                )
                .scalars()
                .all()
            )
            idx = set(
                conn.execute(text("SELECT indexname FROM pg_indexes WHERE schemaname='public'"))
                .scalars()
                .all()
            )
    assert {
        "audit_records",
        "training_records",
        "playbooks",
        "approvals",
        "correlation_baseline",
    } <= tables
    assert {"ix_audit_correlation_id", "ix_training_signature", "ix_approvals_status"} <= idx
