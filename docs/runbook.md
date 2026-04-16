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
- **Readiness**: `GET /ready` → `{"status": "ready"}` (200) when Ollama is
  reachable within the probe TTL (`OLLAMA_PROBE_TTL_SECONDS`, default 10 s),
  or `{"status": "not_ready", "reason": "ollama_unreachable"}` (503) otherwise

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

## Degraded-Mode Startup

The service starts successfully even when Ollama is unreachable at boot.
This is intentional — not a crash-loop condition.

**What you will see:**

- `ollama_unreachable_at_startup` (WARNING) in logs during boot — the
  startup probe failed, the service continues in degraded mode.
- `GET /health` → 200 (`{"status": "ok"}`) — the process is alive.
- `GET /ready` → 503 (`{"status": "not_ready", ...}`) — the service
  is not yet ready to serve extraction requests.
- `POST /api/v1/extract` → 503 (`INTELLIGENCE_UNAVAILABLE`) — extraction
  requests are cleanly rejected.

**Self-healing:** When Ollama becomes reachable and the next readiness
probe succeeds (after `OLLAMA_PROBE_TTL_SECONDS` expires), `/ready`
flips to 200 and extraction requests succeed.  The log emits
`ollama_reachable_recovered` (INFO) on recovery and
`ollama_became_unreachable` (WARNING) if it flips back.

**Operator action:** If `/ready` stays 503 indefinitely, follow the
Ollama troubleshooting steps below.  The service does not need a restart
to recover — it self-heals automatically.

## Benchmarking

`task bench` runs a local latency benchmark against the extraction service.
It sends sequential HTTP requests using a 3-PDF fixture corpus (native
digital, scanned, table-heavy) and prints p50 / p95 latency, annotation
overhead, and service RSS for spot-checking against NFR targets.

**Prerequisites:** Ollama must be running with the configured model pulled.

```bash
# 1. Start the service with the bench skill loaded
task bench:serve
# (in a second terminal)

# 2. Find the service PID (for RSS measurement)
SERVICE_PID=$(pgrep -f "uvicorn app.main:app")

# 3. Run the benchmark
task bench BENCH_SERVICE_PID=$SERVICE_PID
# or without service RSS:
task bench
```

The report compares measured values against these NFR targets:

| NFR | Metric | Target |
|-----|--------|--------|
| NFR-004 | Native digital P50 / P95 | <= 20 s / <= 45 s |
| NFR-005 | Scanned P50 / P95 | <= 60 s / <= 120 s |
| NFR-006 | Annotation overhead (PDF_ONLY P50 - JSON_ONLY P50) | <= 2 s |
| NFR-008 | Service idle RSS (requires `--service-pid`) | <= 1.5 GB |

If numbers drift from these targets, investigate as a potential regression.
The benchmark is NOT wired into `task check` — it requires real Ollama and
measures machine-dependent latency. It is part of the developer dev-loop
and the maintainer's release sanity check.

## Troubleshooting

### Backend won't start

- Run `uv sync --dev` in `apps/backend/`.
- Check that `apps/backend/skills/` exists if any skills have been authored
  (skill manifest validation happens at startup; a broken skill kills the
  boot).

### Tests fail with import errors

- Run `uv sync --dev` in `apps/backend/`.
- Remove `.pytest_cache/` and retry.
