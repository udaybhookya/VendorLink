# One image per service: docker build --build-arg SERVICE=trust_gateway -f infra/Dockerfile .
FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra gcp --no-install-project

COPY packages ./packages
COPY agents ./agents
COPY services ./services
COPY scripts ./scripts
COPY config ./config
COPY seed ./seed
RUN uv sync --frozen --no-dev --extra gcp

ARG SERVICE
ENV SERVICE=${SERVICE} PORT=8080 VL_CONFIG_DIR=/app/config VL_DATA_DIR=/tmp/vl_data \
    PATH=/app/.venv/bin:$PATH
RUN useradd --uid 10001 --create-home app && chown -R app /app
USER app
# Run from the venv directly: `uv run` would need a writable cache dir at startup.
CMD ["sh", "-c", "exec uvicorn ${SERVICE}.app:app --host 0.0.0.0 --port ${PORT} --no-access-log --timeout-graceful-shutdown 5"]
