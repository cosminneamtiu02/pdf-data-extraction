---
type: epic
id: PDFX-E007
parent: PDFX
title: Platform, Observability & Quality Gates
status: not-started
priority: 70
dependencies: [PDFX-E006]
---

## Short description

Wrap the extraction service in the operational envelope that makes it production-shaped for a developer-machine deployment: `/health` and `/ready` endpoints with a TTL-gated Ollama probe, degraded-mode startup, structured request logging with content redaction, `import-linter` contracts enforcing the vertical-slice architecture, and a local latency benchmark script against fixture PDFs.

## Description

This Epic owns everything that lives *around* the extraction pipeline rather than *inside* it. It defines the liveness/readiness endpoints — `GET /health` always returns 200 when the process is alive, `GET /ready` returns 200 only when the last Ollama probe succeeded within a configurable TTL (default 10 seconds). It implements the degraded-mode boot semantics: if Ollama is unreachable when the container starts, the service starts anyway with `/health` green and `/ready` red, requests return `INTELLIGENCE_UNAVAILABLE`, and the service self-heals as soon as the next successful probe flips `/ready` green. It adds the request-scoped `request_id` middleware and wires structlog so every log line emitted during a request includes the correlation id; it enforces log content redaction (no raw PDF bytes, no extracted field values, no full prompts in logs). It defines the full set of `import-linter` contracts that enforce the vertical-slice architecture: feature independence (`features.extraction` cannot import from any other feature), intra-feature DAG (router → service → subpackages, with coordinates allowed to import from parsing and extraction, annotation allowed to import from schemas, and leaf subpackages forbidden from importing siblings), and third-party containment (Docling in `docling_document_parser.py`, PyMuPDF in `pdf_annotator.py` plus optional parser preflight, LangExtract in `extraction_engine.py` plus plugin registration, Ollama client in `ollama_gemma_provider.py`). Finally, it delivers a local benchmark script (not a CI gate) that runs the service against a fixture PDF corpus — one native digital, one scanned, one table-heavy — and reports p50/p95 latency and memory deltas for spot-checking against the NFR targets.

## Rationale

This Epic exists because the service needs to *behave well* under real deployment, not just *function correctly* in a unit test. The distinction between `/health` (liveness) and `/ready` (readiness gated on Ollama) is the difference between an orchestrator restarting a crashed container vs. holding traffic off a container whose external dependency is temporarily down — both are real operational cases. Structured logging with redaction is both an operational need (debugging production issues) and a security requirement (NFR-019: never leak document content to logs). The `import-linter` contracts are what turn the vertical-slice architecture from a convention into a mechanically-enforced rule, and they must exist as a single coherent set in one configuration file even though the individual rules are motivated by different upstream Epics. The benchmark script exists because latency targets in the requirements spec are *operational goals*, not CI gates — the way to spot a regression is to run the script and eyeball the numbers. This Epic traces to project success criteria **"cold start to `/ready` green ≤ 10 s"** (readiness probe design), **"idle RSS ≤ 1.5 GB"** (benchmark measurement), **"`task check` passes cleanly"** (import-linter enforcement), and indirectly all latency criteria (via the benchmark script that lets a human verify them).

## Boundary

**In scope:** `app/api/health_router.py` extended (or replaced) with the new `/health` and `/ready` routes; the Ollama probe implementation and its TTL caching; degraded-mode startup behavior; `app/api/middleware.py` for request-id propagation; structlog configuration ensuring request-id is bound to every log emitted during a request; log content redaction policy enforcement (unit tests inspecting log records for forbidden content); `apps/backend/architecture/import-linter-contracts.ini` with the full contract set for the extraction feature; the local benchmark script (`apps/backend/scripts/benchmark.py` or similar, not wired into `task check`); fixture PDF corpus for benchmarking (kept under `tests/fixtures/` or `apps/backend/fixtures/`); integration tests that verify `/health` and `/ready` behavior including the unreachable-Ollama path and the self-heal path; addition of the `structured_output_max_retries`, `max_pdf_bytes`, `max_pdf_pages`, `ollama_probe_ttl_seconds`, and related config fields to `Settings` and `.env.example`.

**Out of scope:** any business-logic changes to the extraction pipeline (everything inside the pipeline lives in PDFX-E002 through PDFX-E006); any CI workflow changes beyond verifying `task check` runs the new import-linter contracts (CI preservation is a PDFX-E001 concern); any fine-grained observability beyond structlog to stdout (no Prometheus, no OpenTelemetry, no APM — explicitly out of scope per NFR-023); any authentication on `/health` or `/ready` (trusted network assumption); any formal SLO on the latency numbers (they're operational goals, not guarantees).

## Open questions

*This list is not exhaustive. Additional questions may surface during feature elicitation.*

- **OQ-002** — Exact Ollama model tag for the smallest Gemma 4 variant at implementation time. Pinned in `.env.example` and documented in the runbook before first successful extraction test.
- **OQ-003** — Default `Settings.ollama_base_url`: `host.docker.internal:11434` (Docker-for-Mac friendly) vs `http://localhost:11434` (non-containerized dev loop). Decided before `infra/compose/docker-compose.yml` is updated. Default: ship `host.docker.internal` and document the alternative.
- Whether the Ollama probe runs on a timer or on-demand when `/ready` is hit. Default: on a lightweight background timer with a TTL, so `/ready` is O(1) and safe to hit aggressively.
- Whether the benchmark script is written in Python or as a shell script. Default: Python, so it can reuse the same fixture-loading utilities as the integration tests.
- Exact request-id generation strategy — ULID vs UUIDv4 vs sortable time-prefixed. Default: UUIDv4 for simplicity; if operators need grep-by-time, upgrade to ULID.
- Whether the import-linter contracts allow `PdfAnnotator` to import from `parsing/` for `BoundingBox` if `BoundingBoxRef` isn't a suitable input. Default: no — pass `BoundingBoxRef` directly, which is already in `schemas/`.
