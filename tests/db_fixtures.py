# tests/db_fixtures.py
"""Shared fixtures for the Postgres-backed store tests.

A single testcontainer Postgres per session (the ~6-8s boot cost, amortized);
the schema is created once via METADATA; each test truncates between runs so
they don't cross-contaminate. Import these where needed, or make them global by
importing from conftest."""

import pytest
from sqlalchemy import text

from common.db import METADATA, make_engine


@pytest.fixture(scope="session")
def postgres_engine():
    from testcontainers.postgres import PostgresContainer


    with PostgresContainer("postgres:16-alpine") as pg:
        # testcontainers returns a psycopg2 URL by default; force psycopg (v3).
        url = pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql+psycopg://")
        engine = make_engine(url)
        METADATA.create_all(engine)
        yield engine
        engine.dispose()


@pytest.fixture()
def clean_db(postgres_engine):
    with postgres_engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE audit_records, training_records, playbooks, approvals, "
                "correlation_baseline RESTART IDENTITY CASCADE"
            )
        )
    yield postgres_engine
