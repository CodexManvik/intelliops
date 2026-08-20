FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY common/ ./common/
COPY services/ ./services/
COPY policies/ ./policies/
COPY deploy/playbooks/ ./data/playbooks/
# Alembic config + migration scripts, so the one-shot `migrate` compose service
# can run `alembic upgrade head` against Postgres before the store services boot.
COPY alembic.ini ./alembic.ini
COPY alembic/ ./alembic/
RUN uv sync --frozen --no-dev

# Launch uvicorn directly from the built venv. Using `uv run` here would try to
# re-sync the environment at container startup (re-resolving/downloading deps),
# which delays or hangs the server bind and trips healthchecks. The venv is
# already populated by `uv sync` above, so call its uvicorn directly.
ENV PATH="/app/.venv/bin:${PATH}"
ENV SERVICE_MODULE=services.ingestion.app:app
ENV PORT=8000
CMD ["sh", "-c", "uvicorn \"$SERVICE_MODULE\" --host 0.0.0.0 --port \"$PORT\""]
