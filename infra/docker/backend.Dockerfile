# ── Build stage ──────────────────────────────────────────────
# python:3.13-slim (pinned 2026-04-17, https://hub.docker.com/_/python)
FROM python:3.13-slim@sha256:d168b8d9eb761f4d3fe305ebd04aeb7e7f2de0297cec5fb2f8f6403244621664 AS builder

# ghcr.io/astral-sh/uv:0.7.2 (pinned 2026-04-17)
COPY --from=ghcr.io/astral-sh/uv:0.7.2@sha256:3b898ca84fbe7628c5adcd836c1de78a0f1ded68344d019af8478d4358417399 /uv /uvx /bin/

WORKDIR /app

# Copy dependency files first for better layer caching
COPY apps/backend/pyproject.toml apps/backend/uv.lock ./

# Install dependencies (without the project itself)
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source and the skill manifest directory. `skills/` is
# load-bearing: `create_app()` calls `SkillLoader.load(settings.skills_dir)`
# at module import time and raises `SkillValidationFailedError` if the
# directory is missing entirely. Shipping an empty `skills/` (just a
# `.gitkeep`) lets the container boot with a `skill_manifest_empty`
# structlog warning until operator-supplied YAMLs are mounted in.
COPY apps/backend/app ./app
COPY apps/backend/skills ./skills

# Install the project
COPY apps/backend/pyproject.toml ./
RUN uv sync --frozen --no-dev

# ── Runtime stage ────────────────────────────────────────────
# python:3.13-slim (pinned 2026-04-17, https://hub.docker.com/_/python)
FROM python:3.13-slim@sha256:d168b8d9eb761f4d3fe305ebd04aeb7e7f2de0297cec5fb2f8f6403244621664

# Run as non-root user
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --create-home appuser

WORKDIR /app

# Copy the virtual environment, application, and skill manifest from the builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/app /app/app
COPY --from=builder /app/skills /app/skills

# Put the venv on PATH
ENV PATH="/app/.venv/bin:$PATH"

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
