# common/db.py
"""SQLAlchemy Core foundation for the Postgres store adapters.

The three tables are defined once here (shared by the adapters and by Alembic's
autogenerate) using the hybrid schema: promoted key columns for indexed queries
plus a JSONB `payload` that is the source of truth for reconstructing the
Pydantic record. Reads always rebuild from `payload`, never from the columns."""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

METADATA = MetaData()

# JSONB on Postgres; falls back to generic JSON on other dialects (none used).
_JSON = JSONB().with_variant(JSON(), "sqlite")

audit_records = Table(
    "audit_records",
    METADATA,
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
    "training_records",
    METADATA,
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
    "playbooks",
    METADATA,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("hitl_mode", String, nullable=False),
    Column("reversible", Boolean, nullable=False),
    Column("payload", _JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

approvals = Table(
    "approvals",
    METADATA,
    Column("id", String, primary_key=True),
    Column("situation_id", String, nullable=False),
    Column("playbook_id", String, nullable=False),
    Column("status", String, nullable=False),
    Column("payload", _JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
Index("ix_approvals_status", approvals.c.status)

correlation_baseline = Table(
    "correlation_baseline",
    METADATA,
    Column("metric_name", String, primary_key=True),
    Column("n", Float, nullable=False),
    Column("mean", Float, nullable=False),
    Column("variance", Float, nullable=False),
    Column("count", BigInteger, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

# A trained model artifact (joblib-serialized IsolationForest + feature metadata)
# keyed by a logical name. LargeBinary -> bytea on Postgres. Best-effort like the
# baseline: a lost flush just means the trained correlator re-fits.
model_artifacts = Table(
    "model_artifacts",
    METADATA,
    Column("name", String, primary_key=True),
    Column("artifact", LargeBinary, nullable=False),  # -> bytea on postgres
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

# The AI runbook author's memory of its own drafting decisions. disposition and
# outcome are promoted typed columns (queried/updated directly by proposal_id /
# playbook_id) that must stay consistent with the JSONB payload — updates are
# read-modify-write within one transaction (see PostgresAuthorDecisionStore).
author_decisions = Table(
    "author_decisions",
    METADATA,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("signature", String, nullable=False),
    Column("proposal_id", String, nullable=False),
    Column("playbook_id", String, nullable=False),
    Column("disposition", String, nullable=False),
    Column("outcome", String, nullable=False),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("payload", _JSON, nullable=False),
    Index("ix_author_decisions_signature", "signature"),
    Index("ix_author_decisions_proposal_id", "proposal_id"),
    Index("ix_author_decisions_playbook_id", "playbook_id"),
)

# The AI runbook author's agent-run trace: one header row per run (agent_runs)
# plus an ordered log of steps (agent_run_steps) — model turns, tool calls, the
# submit, and the final outcome. run_id is a plain indexed String (not the PK;
# `id` autoincrement is the PK per the file's existing pattern) so a run can be
# looked up without relying on insertion order. step_count is a promoted counter
# on the header, bumped by PostgresTraceStore.append_step in the same
# transaction as the step insert — kept for cheap feed rendering (recent_runs)
# without a COUNT subquery; payload remains the source of truth on read.
agent_runs = Table(
    "agent_runs",
    METADATA,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("run_id", String, nullable=False),
    Column("signature", String, nullable=False),
    Column("status", String, nullable=False),
    Column("proposal_id", String, nullable=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("step_count", Integer, nullable=False, default=0),
    Column("payload", _JSON, nullable=False),
    Index("ix_agent_runs_run_id", "run_id"),
    Index("ix_agent_runs_started_at", "started_at"),
)

agent_run_steps = Table(
    "agent_run_steps",
    METADATA,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("run_id", String, nullable=False),
    Column("seq", Integer, nullable=False),
    Column("kind", String, nullable=False),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("payload", _JSON, nullable=False),
    Index("ix_agent_run_steps_run_id", "run_id"),
)


def make_engine(database_url: str) -> Engine:
    return create_engine(database_url, future=True, pool_pre_ping=True)


def to_payload(model: BaseModel) -> dict:
    return model.model_dump(mode="json")


def from_payload(payload: dict, model_cls: type[BaseModel]) -> BaseModel:
    return model_cls.model_validate(payload)
