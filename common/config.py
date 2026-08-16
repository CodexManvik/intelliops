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
    # A gauge query: cpu_usage keeps its __name__ (so the source maps a real
    # metric name, not "unknown") and its labels (job/service), and it spikes
    # when the demo target breaks. A rate() query would strip __name__, which
    # leaves correlation with a nameless, label-less series it cannot classify.
    prometheus_query: str = "cpu_usage"
    telemetry_poll_seconds: float = 5.0
    # Correlation tuning. Defaults preserve production behavior (a long warm-up
    # so a cold service doesn't emit spurious anomalies); a live demo overrides
    # these via env to detect an injected incident within a minute or two.
    correlation_warmup_samples: int = 50
    correlation_z_threshold: float = 3.0
    correlation_window_seconds: float = 30.0
    governance_mode: str = "in_process"  # "in_process" | "http"
    governance_url: str = "http://localhost:8005"
    read_outcomes_max: int = 200
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
