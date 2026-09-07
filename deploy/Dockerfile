# syntax=docker/dockerfile:1
FROM python:3.11-slim AS builder-base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./

# ---- base: 12 common deps, NO ml/k8s extras (11 of 13 services) ----
FROM builder-base AS base
RUN uv sync --frozen --no-dev --no-install-project

COPY common/ ./common/
COPY services/ ./services/
COPY policies/ ./policies/
COPY deploy/playbooks/ ./data/playbooks/
# Alembic config + migration scripts, so the one-shot `migrate` compose service
# can run `alembic upgrade head` against Postgres before the store services boot.
COPY alembic.ini ./alembic.ini
COPY alembic/ ./alembic/
RUN uv sync --frozen --no-dev --no-install-project

# Launch uvicorn directly from the built venv. Using `uv run` here would try to
# re-sync the environment at container startup (re-resolving/downloading deps),
# which delays or hangs the server bind and trips healthchecks. The venv is
# already populated by `uv sync` above, so call its uvicorn directly.
ENV PATH="/app/.venv/bin:${PATH}"
ENV SERVICE_MODULE=services.ingestion.app:app
ENV PORT=8000
CMD ["sh", "-c", "uvicorn \"$SERVICE_MODULE\" --host 0.0.0.0 --port \"$PORT\""]

# ---- full: base + ml + k8s (rca: embedding selection; action: k8s remediation) ----
FROM builder-base AS full
RUN uv sync --frozen --no-dev --extra ml --extra k8s --no-install-project

COPY common/ ./common/
COPY services/ ./services/
COPY policies/ ./policies/
COPY deploy/playbooks/ ./data/playbooks/
COPY alembic.ini ./alembic.ini
COPY alembic/ ./alembic/
RUN uv sync --frozen --no-dev --extra ml --extra k8s --no-install-project

# torch is used for INFERENCE ONLY (sentence-transformers .encode for cosine fit;
# no training/CUDA). pyproject.toml pins torch to the CPU-only PyTorch index
# (`[tool.uv.sources]` + `[[tool.uv.index]] pytorch-cpu`), so the `uv sync` above
# ALREADY installs `torch ...+cpu` and never downloads the multi-GB CUDA wheels
# (the CUDA build is ~6GB; this CPU image measures ~4GB). Assert it here as a build-time guard.
RUN /app/.venv/bin/python -c "import torch; assert torch.version.cuda is None and torch.__version__.endswith('+cpu'), f'expected CPU-only torch, got {torch.__version__} (cuda={torch.version.cuda})'; print('torch OK:', torch.__version__)"

# Pre-cache all-MiniLM-L6-v2 (~90MB) so RUNBOOK_SELECTOR_MODE=embedding needs no
# runtime HuggingFace egress. Set a stable cache dir the runtime also uses.
# NOTE: PATH isn't pointed at /app/.venv/bin until below, so invoke the venv's
# python explicitly here (bare `python` would hit the base image's system
# interpreter, which has none of these packages installed).
ENV HF_HOME=/app/.cache/huggingface
ENV SENTENCE_TRANSFORMERS_HOME=/app/.cache/sentence-transformers
RUN /app/.venv/bin/python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')" \
    && test -d "$SENTENCE_TRANSFORMERS_HOME" -o -d "$HF_HOME/hub"
# The model is baked, so never reach out to HuggingFace at runtime (avoids a
# name-resolution stall + a noisy warning on each cold start in an air-gapped
# cluster). Loads straight from the cache above.
ENV HF_HUB_OFFLINE=1
ENV TRANSFORMERS_OFFLINE=1

ENV PATH="/app/.venv/bin:${PATH}"
ENV SERVICE_MODULE=services.ingestion.app:app
ENV PORT=8000
CMD ["sh", "-c", "uvicorn \"$SERVICE_MODULE\" --host 0.0.0.0 --port \"$PORT\""]
