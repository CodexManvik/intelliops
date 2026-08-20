# common/db.py
"""SQLAlchemy Core foundation for the Postgres store adapters.

The three tables are defined once here (shared by the adapters and by Alembic's
autogenerate) using the hybrid schema: promoted key columns for indexed queries
plus a JSONB `payload` that is the source of truth for reconstructing the
Pydantic record. Reads always rebuild from `payload`, never from the columns."""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import (
    JSON, BigInteger, Boolean, Column, DateTime, Index, MetaData, String, Table,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

METADATA = MetaData()

# JSONB on Postgres; falls back to generic JSON on other dialects (none used).
_JSON = JSONB().with_variant(JSON(), "sqlite")

audit_records = Table(
    "audit_records", METADATA,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("correlation_id", String, nullable=False),
    Column("actor", String, nullable=False),
    Column("action", String, nullable=False),
    Column("resource", String, nullable=False),
    Column("decision", String, nullable=False),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("payload", _JSON, nullable=False),
    Index("ix_audit_correlation_id", "correlation_id"),
    Index("ix_audit_ts", "ts"),
)

training_records = Table(
    "training_records", METADATA,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("situation_id", String, nullable=False),
    Column("signature", String, nullable=False),
    Column("playbook_id", String, nullable=False),
    Column("result", String, nullable=False),
    Column("worked", Boolean, nullable=False),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("payload", _JSON, nullable=False),
    Index("ix_training_signature", "signature"),
)

playbooks = Table(
    "playbooks", METADATA,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("hitl_mode", String, nullable=False),
    Column("reversible", Boolean, nullable=False),
    Column("payload", _JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


def make_engine(database_url: str) -> Engine:
    return create_engine(database_url, future=True, pool_pre_ping=True)


def to_payload(model: BaseModel) -> dict:
    return model.model_dump(mode="json")


def from_payload(payload: dict, model_cls: type[BaseModel]) -> BaseModel:
    return model_cls.model_validate(payload)
