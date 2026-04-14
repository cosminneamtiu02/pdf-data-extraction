---
type: project
slug: PDFX
title: PDF Data Extraction Microservice
status: fully-detailed
---

## Short description

A self-hosted, skill-driven HTTP microservice that converts PDFs into structured JSON with source-grounded highlights, designed to be embedded in downstream projects as reusable infrastructure.

## Core idea

Callers POST a PDF, a skill name, a skill version, and an output mode to a single FastAPI endpoint. The service parses the PDF via Docling (handling both native digital PDFs and scanned PDFs via OCR automatically), orchestrates extraction via LangExtract against a locally running Gemma 4 model through a custom Ollama community provider plugin, maps the extracted values back to PDF-page coordinates, optionally draws PyMuPDF highlights on the original PDF at the exact locations the data came from, and returns one of three outputs depending on the output mode: structured JSON, an annotated PDF binary, or both. Every field declared in the skill's output schema is always present in the response — never silently dropped — with per-field `status`, `source` (document vs inferred), and `grounded` flags. The service is generic by design: the skill system is a data layer authored by the integrating caller, and the pipeline is domain-neutral.

## Problem being solved

Downstream software projects that need PDF extraction as a feature currently have three bad paths: send the content to a managed cloud OCR+LLM service (paying per call and leaking document content), hand-roll a pipeline on top of a local PDF parser plus a local LLM (duplicating orchestration, chunking, multi-pass, and source grounding logic each time), or build the extraction into the host application directly (coupling extraction to one project's codebase so the work cannot be reused). Each project wastes engineering effort on infrastructure that should exist once and be embedded wherever needed. This project exists to be that once-built, embed-everywhere piece — stateless, self-hosted, grounding-aware, and skill-driven — so that any future project can get PDF-to-structured-data extraction as a configured step rather than a re-implementation.

## Intended users

A single persona: the **integrating developer** (P-01 in the requirements spec). This is a software engineer, backend or full-stack, who embeds the service into a downstream project and authors skill YAML files describing what to extract for each document type their project handles. They call the service over HTTP from their own code, never directly from a human-facing UI. Their environment is a developer workstation running macOS, Linux, or Windows, with Ollama installed on the host and the microservice running either as a Python process or inside a Docker container that talks to host-side Ollama. They are comfortable with HTTP APIs, multipart uploads, JSON schemas, OpenAPI, and YAML; they are able to run and troubleshoot Ollama locally. No human end users interact with this service — all interaction is code-to-HTTP.

## Known constraints

- **Runtime stack (locked).** Python 3.13; `uv` for dependency management; FastAPI + Pydantic v2 + pydantic-settings + structlog as the API-layer stack. No alternatives considered.
- **Extraction stack (locked).** Docling for PDF parsing and OCR; LangExtract for extraction orchestration; Ollama (external to the container, running on the host) as the only LLM runtime; smallest Gemma 4 variant as the default model (configurable via `Settings.ollama_model`, never hardcoded); PyMuPDF for PDF annotation.
- **Monorepo tooling (preserved where load-bearing).** The template's CI, Dependabot auto-merge, pre-commit, and Taskfile runner are preserved unchanged. The original `pnpm 10` + Node 22 workspace shell was removed during the E001 cleanup (commit `f4813d1`) once the frontend and `api-client` packages were deleted — there is no JavaScript artifact left to manage, so the `package.json` / `pnpm-workspace.yaml` / `pnpm-lock.yaml` files no longer exist at the repo root. The repository remains a monorepo by directory layout (`apps/backend/`, `packages/error-contracts/`, `infra/`) but is Python-only at the package-manager layer.
- **Storage model.** No database, no persistent storage, no disk writes outside logging, no request history, no caching across requests. The service writes zero bytes to persistent disk during request handling.
- **Network posture.** Self-hosted only. Zero cloud runtime dependencies. No outbound network calls except to the configured local Ollama base URL. Trusted-network deployment assumption: the service is deployed on the caller's own machine or inside a trusted container boundary, never directly exposed to the open internet.
- **Request model.** Synchronous, atomic, `workers=1` single-instance concurrency. No async job queue, no background workers, no streaming responses.
- **Security model.** No authentication, authorization, rate limiting, or multi-tenancy. Any such concerns are the caller's deployment responsibility.
- **Hard operational limits.** `max_pdf_bytes = 50 MB`, `max_pdf_pages = 200`, `extraction_timeout_seconds = 180`.
- **Architecture discipline (from CLAUDE.md).** Vertical-slice architecture enforced by `import-linter`; feature independence; intra-feature layer DAG; third-party dependency containment (Docling, PyMuPDF, LangExtract, Ollama client each confined to one implementation file). One class per file. TDD red-green-refactor for every class. No paradigm drift. `task check` must pass before any work is declared done. Never use `--no-verify`.
- **Error system.** All errors live in `packages/error-contracts/errors.yaml` and are codegenerated via `task errors:generate`; no hand-edited `_generated/` files; translation files must be kept in sync across all locales.
- **Repository conventions are defined in CLAUDE.md. Feature bundles must not contradict CLAUDE.md.**
- **All code must be fully type-annotated. Pydantic models for data shapes. Pyright strict must pass.**

## Rejected directions

- **Managed cloud OCR or LLM APIs (e.g. Google Document AI, Anthropic API, OpenAI).** Rejected because self-hosting is a primary requirement — cloud dependencies violate the cost, privacy, and autonomy goals that justify the project's existence.
- **A second `IntelligenceProvider` implementation (Claude, GPT-4, other) in v1.** Rejected because the protocol is already designed to accept one later; shipping two doubles the validation effort without a concrete downstream need. Deferred to v2.
- **Asynchronous job queue or background workers.** Rejected because the service's stateless design is a core value; adding a queue would require persistent state, job reconciliation, and a separate worker process, none of which justify their cost for a single-request synchronous workload.
- **Frontend, CLI tool, or human-facing UI.** Rejected because every caller is code, not a human; the service ships an HTTP API and an OpenAPI document and nothing else.
- **Per-skill `allow_inference: bool` gating of ungrounded values.** Rejected for v1 because the response already includes `source=inferred` and `grounded=false` flags; per-skill policy is additional complexity with no immediate consumer. Deferred to v2.
- **Database-backed skill manifest.** Rejected because skills are file-system data, validated at container startup, and work cleanly with zero runtime state.
- **Using LangExtract's Gemini-specific controlled generation features.** Rejected because Ollama + Gemma 4 does not expose controlled generation natively; the project compensates with a provider-agnostic `StructuredOutputValidator` that cleans model output and retries up to four total attempts against the skill's JSONSchema.
- **Hot-reloading skill YAMLs without container restart.** Rejected for v1 because restart is cheap and the complexity of a safe hot-reload (atomic manifest swap, in-flight request handling) is not justified by any current use case.

## Success criteria

- **Native digital PDF extraction latency:** `p50 ≤ 20 s`, `p95 ≤ 45 s` for a 10-page PDF against a typical invoice-style skill. Measured by local benchmark against a fixture corpus.
- **Scanned PDF extraction latency:** `p50 ≤ 60 s`, `p95 ≤ 120 s` for a 10-page PDF with OCR engaged. Measured the same way.
- **Cold-start latency:** `≤ 10 s` from container boot to `/ready` green state, assuming Ollama is reachable and `SkillManifest` validation completes.
- **Idle process memory footprint:** `≤ 1.5 GB` RSS after warmup with no in-flight request.
- **Structured output success rate:** `≥ 90%` per-field success (fields that reach `status=extracted`) for well-authored skills applied to matched document types.
- **Coordinate grounding rate:** `≥ 95%` of successfully extracted fields resolve to at least one `BoundingBoxRef` on native digital PDFs; `≥ 85%` on scanned PDFs.
- **Quality gates:** `task check` passes cleanly on every PR (ruff format + lint, pyright strict, import-linter contracts, unit + integration + contract tests, error-contract validation).
- **API stability:** Every field declared in a skill's `output_schema` is always present in the response (no silent drops), with predictable per-field `status`, `source`, `grounded`, and `bbox_refs`, so downstream consumers can write a stable database schema against the skill.

## Scale & scope expectations

The service is a **single-instance, single-in-flight-request** component. Uvicorn is configured with `workers=1` by default; concurrent requests queue in FastAPI's event loop and serve serially. There is no throughput SLO and no uptime SLA — callers who need higher throughput can run multiple instances behind a load balancer (architecturally permitted, not validated). Per-request memory stays under 2.5 GB RSS peak; the service's idle warm RSS stays under 1.5 GB. PDFs are capped at 50 MB and 200 pages to prevent pathological workloads from blowing through the 180-second end-to-end timeout. The service is designed to be deployed on a single developer machine or a single small container host, running alongside Ollama on the same machine. Horizontal scalability, multi-tenancy, persistent caching, cross-request analytics, and any form of orchestration beyond "a container that accepts HTTP requests" are explicitly out of scope for v1.

## Open questions carried into the graph

- **OQ-002 — Exact Ollama model tag.** The tag list on the Ollama registry evolves over time; the default `Settings.ollama_model` value must be pinned to a concrete smallest-Gemma-4 tag at implementation time, documented in `.env.example` and the runbook. Mirrored into the Platform epic.
- **OQ-003 — Default `ollama_base_url`.** `host.docker.internal:11434` (Docker-for-Mac) vs `http://localhost:11434` (non-containerized dev loop). Decided before `infra/compose/docker-compose.yml` is written. Mirrored into the Platform epic.
- **OQ-004 — Correction prompt wording.** Exact `CorrectionPromptBuilder` template for retry attempts; needs empirical tuning against Gemma 4 failure modes. Mirrored into the Intelligence & Extraction epic.
- **OQ-005 — Fate of `app/shared/base_service.py`.** Whether the existing template file survives the cleanup depends on whether its interface is DB-coupled. Default action: remove. Mirrored into the Template Cleanup epic.
