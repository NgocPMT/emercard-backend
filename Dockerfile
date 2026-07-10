FROM ghcr.io/astral-sh/uv:0.11.19 AS uv

FROM python:3.14.6-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=uv /uv /uvx /usr/local/bin/
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN uv sync --locked --no-dev

EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn emercard.main:app --host ${EMERCARD_HOST:-0.0.0.0} --port ${PORT:-${EMERCARD_PORT:-8000}}"]
