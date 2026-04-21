# ── Build stage ──────────────────────────────────────────────
# Sourced once as an `ARG` so the builder and runtime stages always point at
# the identical digest. Updating the pin means editing one line.
# python:3.13-slim (pinned 2026-04-17, https://hub.docker.com/_/python)
ARG PYTHON_IMAGE=python:3.13-slim@sha256:d168b8d9eb761f4d3fe305ebd04aeb7e7f2de0297cec5fb2f8f6403244621664

FROM ${PYTHON_IMAGE} AS builder

# ghcr.io/astral-sh/uv:0.7.2 (pinned 2026-04-17)
COPY --from=ghcr.io/astral-sh/uv:0.7.2@sha256:3b898ca84fbe7628c5adcd836c1de78a0f1ded68344d019af8478d4358417399 /uv /uvx /bin/

WORKDIR /app

# Copy dependency files first for better layer caching
COPY apps/backend/pyproject.toml apps/backend/uv.lock ./

# Install dependencies (without the project itself). For the torch CPU-wheel
# routing rationale, see the [tool.uv.index] + [tool.uv.sources] block in
# apps/backend/pyproject.toml (also issue #139).
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source and the skill manifest directory. `skills/` is
# load-bearing: `create_app()` calls `SkillLoader.load(settings.skills_dir)`
# at module import time and raises `SkillValidationFailedError` if the
# directory is missing entirely. Shipping an empty `skills/` (just a
# `.gitkeep`) lets the container boot with a `skill_manifest_empty`
# structlog warning until operator-supplied YAMLs are mounted in.
COPY apps/backend/app ./app
COPY apps/backend/skills ./skills

# Install the project. `pyproject.toml` is already present in the image
# from the earlier dependency-file COPY above, so there is no need to
# copy it again — `uv sync` consumes it in place.
RUN uv sync --frozen --no-dev

# ── Runtime stage ────────────────────────────────────────────
# Reuses the top-level `ARG PYTHON_IMAGE` (declared before any `FROM`), which
# per the Dockerfile ARG scoping rules is available for substitution in every
# stage's `FROM` line — keeping builder and runtime pinned to the same digest.
FROM ${PYTHON_IMAGE}

# Install Tesseract OCR so Docling's `TesseractCliOcrOptions` path can shell out
# to the `tesseract` binary at OCR time (see docs/decisions.md ADR-013 and
# issue #106). The CLI variant is deliberately chosen over `TesseractOcrOptions`
# (which requires `tesserocr` Python bindings built against
# libtesseract-dev/libleptonica-dev) and over EasyOCR (which pulls ~1 GB of
# torch/opencv extras on top of the CPU torch wheels already pinned — undoing
# the image-size work in issue #139). `tesseract-ocr-eng` supplies the English
# language data; add more `tesseract-ocr-<lang>` packages here if the service
# ever needs to OCR non-English PDFs, and set `TESSDATA_PREFIX` accordingly.
#
# `tini` is the PID 1 init wrapper (issue #213). It does two things the
# bare `uvicorn` PID 1 cannot:
#   1. Forwards `docker stop` SIGTERM / SIGINT to its direct child (uvicorn).
#      A bare PID 1 has no default signal handlers in the kernel and can
#      silently drop signals unless the program installs handlers itself.
#      tini does NOT forward signals to grandchildren — sub-processes that
#      Docling / PyMuPDF / OCR tooling spawn during extraction are signaled
#      only via uvicorn's own worker-shutdown path, not by tini directly.
#      (Tini's `-g` flag would forward to the entire process group, but we
#      deliberately do not enable it: graceful shutdown should let in-flight
#      OCR/Docling subprocesses finish or time out via uvicorn's teardown,
#      not be killed mid-run.)
#   2. Reaps adopted orphan processes. Any sub-process whose parent dies
#      before reaping it is re-parented to PID 1; uvicorn does not reap, so
#      without tini those zombies accumulate.
# tini is a tiny (~10 KB) binary; apt's Debian-stable package is kept to
# just the binary via `--no-install-recommends`, respecting the
# #139 / #192 image-size budget.
#
# `curl` is installed so the HEALTHCHECK (below) can probe `/health`
# without spawning the full app venv Python interpreter every 30s
# (issue #363). Using the dedicated `curl` CLI keeps each probe much
# lighter than invoking `python -c ...` against the full Docling +
# LangExtract + PyMuPDF + torch CPU-wheel venv, and avoids extra
# interpreter startup work that can race against `docker stop` during
# graceful shutdown. `--no-install-recommends` keeps the apt layer to
# just the `curl` binary and its already-present libc/TLS shared libs.
#
# reproducibility-boundary:digest-only (issue #362)
# -------------------------------------------------
# The apt packages below are INTENTIONALLY not pinned to `pkg=version`
# tuples. That means rebuilds are expected to pick up newer Debian
# package versions over time, by design, when `apt-get update` reads
# from the default moving apt repositories. This is how the image
# receives Debian security fixes between base-image pin bumps.
#
# The `@sha256:…` digest on the `python:3.13-slim` base declared at the
# top of this file pins the starting filesystem for both stages, but it
# does NOT pin the apt repository snapshot consulted during
# `apt-get update`. So an unchanged base-image digest does not
# guarantee identical apt results or byte-for-byte identical layers
# across rebuilds.
#
# Full reproducibility for these apt installs would require pointing
# apt at a snapshot repository (for example `snapshot.debian.org` or
# another dated mirror) and/or pinning exact package versions as
# `pkg=version`. We intentionally do not do that here because the
# desired policy is to keep apt packages floating for security updates.
#
# If a future contributor needs to pin a package as `pkg=version`
# and/or switch to a snapshot apt repository, the correct sequence is:
#   1. Explain the reproducibility/security tradeoff in the PR
#      description (pins buy determinism at the cost of Debian
#      security updates, and can become brittle as repositories rotate
#      older builds).
#   2. Update the `apps/backend/tests/unit/meta/`
#      `test_dockerfile_apt_intentional_floating.py` guard rails to
#      match the new policy.
# Skipping any of those steps means the test below will fire. That is
# the intended behavior — the marker string on the first line of this
# block (`reproducibility-boundary:digest-only`) is the anchor the
# test greps.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        curl \
        tesseract-ocr \
        tesseract-ocr-eng \
        tini \
    && rm -rf /var/lib/apt/lists/*

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

# Exec-form HEALTHCHECK (issue #363): invokes `curl` directly instead of
# wrapping the probe in `/bin/sh -c`, and — more importantly — avoids
# re-spawning a cold Python interpreter against the ~500 MB app venv
# (Docling + LangExtract + PyMuPDF + torch CPU wheels) every 30s.
# `curl -fsS` exits non-zero on HTTP 4xx/5xx responses (3xx redirects
# are followed/allowed only with `-L`, which we do not set), and `-s`
# silences the progress bar while `-S` preserves error messages on
# failure. `--output /dev/null` discards the response body so the probe
# does not spam Docker's healthcheck log with `{"status":"ok"}` every
# 30s. `/health` is the right endpoint for container-level liveness
# (the compose-level healthcheck hits `/ready` for readiness — see
# infra/compose/docker-compose.prod.yml). `curl` is installed above.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD ["curl", "-fsS", "--output", "/dev/null", "http://localhost:8000/health"]

# Exec-form ENTRYPOINT so Docker does not wrap it in /bin/sh (which would
# insert a shell as PID 1 and defeat the point of tini). The `--` sentinel
# tells tini to treat the remaining CMD argv literally, even if a CMD
# element starts with a dash. tini forwards SIGTERM / SIGINT to its direct
# child (uvicorn) and reaps orphan processes adopted by PID 1 (issue #213);
# see the runtime-stage comment block above for why we do NOT enable tini's
# `-g` process-group forwarding. CMD is preserved as-is so compose / k8s
# overrides of the uvicorn argv keep working.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
