"""Meridian sample-system tables, attached to common.db.METADATA.

Meridian is the Deloitte-style financial-reporting platform IntelliOps
monitors as a realistic sample target. These tables are intentionally
minimal — just enough for the gateway/validation/aggregation/reporting
services to have something to persist in a later task. They live on the
shared METADATA object so alembic/env.py's `target_metadata = METADATA`
autogenerate picks them up alongside the core IntelliOps tables.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Column, DateTime, Float, String, Table

from common.db import METADATA

meridian_submissions = Table(
    "meridian_submissions",
    METADATA,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("client", String, nullable=False),
    Column("period", String, nullable=False),
    Column("status", String, nullable=False),
    Column("amount", Float, nullable=False),
    Column("ts", DateTime(timezone=True), nullable=False),
)

meridian_reports = Table(
    "meridian_reports",
    METADATA,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("submission_id", BigInteger, nullable=False),
    Column("summary", String, nullable=False),
    Column("ts", DateTime(timezone=True), nullable=False),
)
