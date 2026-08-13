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


@lru_cache
def get_settings() -> Settings:
    return Settings()
