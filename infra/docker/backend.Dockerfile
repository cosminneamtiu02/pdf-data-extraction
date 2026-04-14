# ── Build stage ──────────────────────────────────────────────
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.7.2 /uv /uvx /bin/

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
FROM python:3.13-slim

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
