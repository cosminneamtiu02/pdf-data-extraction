# Runbook

Operational guide for running and maintaining the PDF data extraction
microservice.

## Prerequisites

- Python 3.13 and `uv` installed.
- Ollama running on the host machine with the smallest Gemma 4 variant pulled.
  The exact model tag is a config value (`OLLAMA_MODEL`) defaulted in
  `apps/backend/app/core/config.py` during feature-dev for PDFX-E004-F002.
- Docker (optional — only if you want to run the service in a container).

## Local Development

```bash
# Backend directly with hot reload (no Docker)
task dev:backend

# Backend in a container (Ollama still runs on the host)
task dev
```

Services:

- API: http://localhost:8000
- API docs: http://localhost:8000/docs (dev only)
- Liveness: http://localhost:8000/health
- Readiness: http://localhost:8000/ready

## Testing

```bash
# Everything task check runs
task check

# Unit only (fast)
task test:unit

# In-process integration (no external services)
task test:integration

# Contract tests
task test:contract
```

## Error System

```bash
# After editing packages/error-contracts/errors.yaml:
task errors:generate
```

## Docker

```bash
# Build the backend image
task docker:build

# Start the stack (backend only; Ollama is on the host)
task docker:up

# Stop the stack
task docker:down
```

## Health Checks

- **Liveness**: `GET /health` → `{"status": "ok"}`
- **Readiness**: `GET /ready` → `{"status": "ready"}` (post-bootstrap stub;
  during feature-dev PDFX-E007-F001 replaces this with an Ollama-probe-gated
  version that returns 503 when Ollama is unreachable)

## Ollama

The service reaches Ollama via the `OLLAMA_BASE_URL` configured in
`Settings` (added during feature-dev for PDFX-E004-F002). Defaults:

- Inside a Docker container: `http://host.docker.internal:11434`
- Non-containerized: `http://localhost:11434`

If `/ready` returns 503 or extraction requests return
`INTELLIGENCE_UNAVAILABLE`, check:

1. Is `ollama serve` running on the host?
2. Is the model tag in `OLLAMA_MODEL` actually pulled? Run `ollama list`.
3. From the container: `curl http://host.docker.internal:11434/api/tags` —
   should return the list of installed models.

## Troubleshooting

### Backend won't start

- Run `uv sync --dev` in `apps/backend/`.
- Check that `apps/backend/skills/` exists if any skills have been authored
  (skill manifest validation happens at startup; a broken skill kills the
  boot).

### Tests fail with import errors

- Run `uv sync --dev` in `apps/backend/`.
- Remove `.pytest_cache/` and retry.
