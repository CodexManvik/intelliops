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

    # --- live-stack settings (test-safe defaults) ---
    telemetry_mode: str = "file"  # "file" | "prometheus"
    prometheus_url: str = "http://localhost:9090"
    prometheus_query: str = (
        "rate(http_request_errors_total[1m]) or on() vector(0)"
    )
    telemetry_poll_seconds: float = 5.0
    governance_mode: str = "in_process"  # "in_process" | "http"
    governance_url: str = "http://localhost:8005"
    read_outcomes_max: int = 200
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
