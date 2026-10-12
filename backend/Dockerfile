FROM python:3.12-slim

# uv from the official image (a single static binary)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Build context is backend/, so these paths are relative to it.
# Dependencies first so this layer is cached until pyproject.toml or uv.lock changes.
COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-dev

COPY app /app/app

# Make `uv run` at start-up reuse the locked, no-dev environment instead of re-syncing it
ENV UV_FROZEN=1 \
    UV_NO_DEV=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    STORAGE_BACKEND=local \
    STORAGE_LOCAL_DIR=/app/course-data

# Download the embedding model into the image (app/embeddings.py caches under /app/.cache/fastembed)
# so the first question does not wait on a ~130 MB download.
RUN uv run python -c "from app.embeddings import get_model; get_model()"

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
