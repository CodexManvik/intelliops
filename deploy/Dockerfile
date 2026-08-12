FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY common/ ./common/
COPY services/ ./services/
RUN uv sync --frozen --no-dev

ENV SERVICE_MODULE=services.ingestion.app:app
ENV PORT=8000
CMD uv run uvicorn "$SERVICE_MODULE" --host 0.0.0.0 --port "$PORT"
