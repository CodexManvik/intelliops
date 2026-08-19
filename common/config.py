"""Runtime configuration, sourced from environment variables."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INTELLIOPS_", env_file=".env")

    redis_url: str = "redis://localhost:6379"
    audit_store_path: str = "data/audit.jsonl"
    playbook_store_path: str = "data/playbooks"
    rbac_policy_path: str = "policies/rbac_policy.yaml"
    rca_context_path: str = "data/rca_context"
    hitl_poll_timeout_seconds: float = 30.0
    hitl_poll_interval_seconds: float = 0.5
    training_store_path: str = "data/training.jsonl"
    reliability_suppress_threshold: float = 0.8
    graduation_min_successes: int = 3


@lru_cache
def get_settings() -> Settings:
    return Settings()
