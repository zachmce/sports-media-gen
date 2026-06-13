# Stage 1: builder — install dependencies using uv
FROM python:3.14-slim-bookworm AS builder

WORKDIR /app

# Bring in the pinned uv shim from the official GHCR image
COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /uvx /usr/local/bin/

# Copy lockfile + pyproject first for layer caching
COPY pyproject.toml uv.lock ./

# Install runtime deps into /app/.venv (no dev deps; no project install yet)
RUN uv sync --frozen --no-dev --no-install-project

# Copy source and README (hatchling build backend validates readme path during build)
COPY README.md ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev

# Stage 2: final runtime image — slim, non-root, WebP-capable
FROM python:3.14-slim-bookworm

# Install runtime shared library for Pillow WebP support
RUN apt-get update && apt-get install -y --no-install-recommends \
    libwebp7 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the installed venv and application source from the builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

# Build-time smoke test: confirm Pillow can import and the WebP plugin is available.
# Must run AFTER copying the venv from builder (Pillow is not installed in the base image).
# WebP is a PIL image plugin module (not a codec), so check_module is correct.
# See: https://pillow.readthedocs.io/en/stable/reference/features.html
RUN /app/.venv/bin/python3 -c "from PIL import Image, features; assert features.check_module('webp'), 'WebP not available'"

# Copy Alembic migration artifacts (the migrate service runs alembic from this image)
COPY alembic.ini ./
COPY migrations/ ./migrations/

# Create a non-root system user for the running container (AGENTS.md hard requirement)
RUN useradd --system --create-home --home-dir /home/appuser appuser

# Switch to non-root before setting env and exposing port
USER appuser

# Make the venv's bin dir first on PATH and expose the src layout package
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src"

EXPOSE 8000

# Default command: gunicorn managing two UvicornWorker processes
CMD ["gunicorn", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "-w", "2", \
     "-b", "0.0.0.0:8000", \
     "matchup_thumbs.main:app"]
