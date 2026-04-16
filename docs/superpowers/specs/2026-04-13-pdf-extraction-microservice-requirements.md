---
project_name: pdf-data-extraction
created: 2026-04-13
status: complete
phases_completed: 8/8
model: claude-sonnet-4-6
total_functional_requirements: 45
total_nonfunctional_requirements: 36
total_scenarios: 32
open_questions: 5
companion_spec: ./2026-04-13-pdf-extraction-microservice-design.md
---

# Requirements Specification: PDF Data Extraction Microservice

> Companion to the architectural design spec at
> [`2026-04-13-pdf-extraction-microservice-design.md`](./2026-04-13-pdf-extraction-microservice-design.md).
> The design spec answers **how the service is built**; this requirements
> spec answers **what the service must do and under what constraints**. They
> are load-bearing together.

## 1. Product Vision

### Problem Statement

Downstream software projects — internal tools, back-office automation scripts, domain-specific workflows — need a reusable building block that converts PDF files into structured JSON with source-grounded highlights, without sending any content to cloud APIs and without imposing a persistent-storage or async-infrastructure footprint on the host. Existing solutions either extract data without source grounding (so callers cannot visually verify the provenance of each field), impose managed cloud dependencies (unacceptable for privacy, cost, or autonomy reasons), or bundle an opinionated ingestion pipeline that forces callers to adopt more than they need. No off-the-shelf piece fits the "stateless, self-hosted, skill-driven, grounding-aware" shape that the ecosystem actually needs.

### Current State

Projects that need PDF extraction today typically pick one of three bad paths: (1) send the PDF to a managed cloud OCR+LLM service, paying per-call and leaking document content; (2) hand-roll a pipeline on top of a PDF text extractor plus a local LLM, duplicating orchestration, chunking, multi-pass, and grounding logic each time; or (3) build the extraction into the host application directly, coupling extraction logic to one project's codebase so the work cannot be reused. All three paths produce brittle solutions and waste engineering effort on infrastructure that should exist once and be embedded everywhere needed.

### Desired Outcome

A self-hosted microservice container that any downstream project can point at, pass a PDF plus a skill reference, and receive back a structured extraction result plus (optionally) an annotated PDF showing exactly where each field came from. The caller owns the skill authoring (YAML files that describe what to extract) and the integration with their own persistence layer; the service owns parsing, orchestration, model invocation, structured output hardening, coordinate matching, and annotation. The service is fully self-contained — Ollama runs on the host, nothing else is required — and makes zero outbound calls to any network beyond the local Ollama daemon.

### Success Metrics

| Metric | Target | Measurement Method | Timeframe |
|---|---|---|---|
| Native-digital PDF extraction latency (10 pages, typical skill) | `p50 ≤ 20 s`, `p95 ≤ 45 s` | Local benchmark against fixture corpus | By first integration test run |
| Scanned PDF extraction latency (10 pages, OCR engaged) | `p50 ≤ 60 s`, `p95 ≤ 120 s` | Same | Same |
| Cold-start latency (boot → `/ready` green) | `≤ 10 s` with Ollama reachable | Integration test measures container boot | First CI run |
| Idle process RSS after warmup | `≤ 1.5 GB` | `ps`/`docker stats` spot check | Documented in runbook |
| Structured output success rate (per-field, across all retries) | `≥ 90%` for well-authored skills against matched document types | Benchmark against fixture corpus | Prior to declaring v1 done |
| Coordinate grounding rate (fields with at least one bbox) | `≥ 95%` for extracted fields on native PDFs; `≥ 85%` on scanned PDFs | Benchmark | Prior to declaring v1 done |
| `task check` passes cleanly | 100% | CI gate | Every PR |

### Explicit Non-Goals

- **No cloud runtime dependency.** The service must not make outbound calls to any network service other than the configured local Ollama base URL. This is non-negotiable — it is the reason the project exists.
- **No persistent storage of any kind.** No database, no disk writes (beyond logging to stdout), no request history, no caching of extractions across requests. Everything lives in memory for the duration of one request.
- **No authentication, authorization, rate limiting, or multi-tenancy.** The service operates under a trusted-network deployment assumption. Exposing it to untrusted callers is explicitly unsupported.
- **No async job queue, no background processing, no streaming responses.** All requests are synchronous and atomic; the response is not returned until the full extraction is complete.
- **No frontend, no CLI tool, no human-facing UI.** The microservice ships an HTTP API and an OpenAPI document. Any UI is the integrating caller's responsibility.
- **No second `IntelligenceProvider` implementation in v1.** The protocol is designed to accept a future Claude or GPT-4 provider, but only the Gemma-4-via-Ollama provider ships in v1.

---

## 2. Stakeholder Map

| ID | Stakeholder | Role | Primary Goals | Key Constraints | Decision Authority |
|---|---|---|---|---|---|
| **S-01** | Integrating developer | Requester, operator, skill author, reviewer, maintainer — all roles collapsed into a single person or development team | Embed this service into a downstream project with minimal ceremony; author skill YAMLs for new document types; trust the output enough to skip manual re-review | Must deploy the service in an environment with Ollama available locally; must respect any data-handling rules that apply to their integrating project | Complete — sole decision-maker for this microservice |

### Stakeholder Conflicts

None detected. The single-stakeholder shape eliminates inter-role tension by construction. There are no upstream sponsors, downstream end-users, operator teams, compliance officers, or product-management voices with competing priorities. Power dynamics are trivial.

---

## 3. Persona Catalogue

### Persona: Integrating Developer (P-01)

- **Role:** Software engineer, backend or full-stack, embedding this microservice into a downstream project. Author of skill YAMLs for each document class their project needs.
- **Context:** At their workstation, building features that include "extract structured data from a PDF" as a step. They call the service from their own code over HTTP — never directly from a browser, never through a UI the microservice ships.
- **Tech Literacy:** High. Comfortable with HTTP APIs, multipart uploads, JSON schemas, Docker, environment variables, YAML, and reading OpenAPI specs. Able to run a local Ollama instance and troubleshoot connectivity. Familiar with Python, FastAPI, pydantic — or at minimum able to debug issues at that layer when integrating with their own stack (which may be in a different language).
- **Usage Frequency:** Varies. During initial integration and skill authoring, several requests per minute while iterating on prompt quality. During steady-state embedding in their downstream project, whatever rate their project produces — could be a single request when a user uploads a PDF, or a batch over a folder of documents.
- **Environment:** A developer workstation running macOS, Linux, or Windows. Ollama installed and running on the host. The microservice itself runs either directly as a Python process or inside a Docker container; the container communicates with host-side Ollama via `host.docker.internal:11434` or equivalent.
- **Primary Goals:**
  - Get structured extraction working for a new document type in as few iterations as possible (author a skill YAML, run an extraction, tweak the prompt, rerun).
  - Trust the output enough to avoid writing their own source-grounding or retry logic.
  - Integrate with the service using whatever HTTP client their project already uses.
  - Understand why things fail when they do — clear error codes, no silent failures.
- **Pain Points:**
  - Existing extraction tools that either don't show source grounding or don't work offline.
  - Prompt engineering loops that are opaque (no visibility into what the model returned or why parsing failed).
  - Tools that require persistent storage, background workers, or cloud accounts just to extract one field from one PDF.
- **What makes them leave:** Unreliable structured output that fails on realistic documents even after retries; coordinate matching that visibly misses the right spot on the page; error responses that are vague or inconsistent; containers that refuse to start or crash mid-request.

---

## 4. Scenario Library

Each scenario maps the service's observable behavior for a specific `(precondition, trigger)` pair. Happy paths, field-level behaviors, failure modes, startup behaviors, and misuse scenarios are all grouped here. Scenarios link back to functional requirements in Section 5.

### SC-001: Native digital PDF, JSON-only extraction (happy path)
- **Type:** happy-path
- **Persona:** P-01
- **Feature Area:** Core extraction
- **Preconditions:**
  - Skill `invoice/1` registered in the `SkillManifest` at startup.
  - Ollama reachable; Gemma 4 smallest variant pulled.
  - PDF is a native (text-layer) PDF of ~10 pages.
- **Trigger:** `POST /api/v1/extract` with `output_mode=JSON_ONLY`.
- **Steps:**
  1. Router parses multipart form, size-checks the upload against `max_pdf_bytes`, hands off to `ExtractionService`.
  2. `SkillLoader.load("invoice", 1)` returns a `Skill`.
  3. `DoclingDocumentParser` parses the PDF; `ParsedDocument` is produced with a list of `TextBlock`s.
  4. `TextConcatenator` joins blocks and builds an `OffsetIndex`.
  5. `ExtractionEngine` invokes LangExtract, which calls `OllamaGemmaProvider` for each chunk; `StructuredOutputValidator` cleans and validates each response.
  6. `SpanResolver` maps raw extraction spans to `BoundingBoxRef`s per field.
  7. Router serializes the `ExtractResponse` as JSON.
- **Expected Outcome:** `200 OK`, `Content-Type: application/json`, body is an `ExtractResponse` containing every field declared in the skill's `output_schema`, each with `value`, `status=extracted`, `source=document`, `grounded=true`, and populated `bbox_refs`.
- **Exceptions:**
  - If any failure mode from SC-013 through SC-026 applies, that scenario's response shape applies instead.
- **Linked Requirements:** FR-001, FR-004, FR-021, FR-024, FR-030, FR-031, FR-034, FR-045

### SC-002: Native digital PDF, annotated-PDF-only output (happy path)
- **Type:** happy-path
- **Persona:** P-01
- **Feature Area:** Core extraction + annotation
- **Preconditions:** Same as SC-001.
- **Trigger:** `POST /api/v1/extract` with `output_mode=PDF_ONLY`.
- **Steps:** Same steps 1–6 as SC-001, then `PdfAnnotator.annotate(pdf_bytes, fields)` draws highlights at each field's `bbox_refs` and returns annotated PDF bytes.
- **Expected Outcome:** `200 OK`, `Content-Type: application/pdf`, body is the annotated PDF bytes. No JSON envelope.
- **Exceptions:** Same as SC-001.
- **Linked Requirements:** FR-001, FR-005, FR-031, FR-038, FR-045

### SC-003: Native digital PDF, multipart output (happy path)
- **Type:** happy-path
- **Persona:** P-01
- **Feature Area:** Core extraction + annotation + multipart response
- **Preconditions:** Same as SC-001.
- **Trigger:** `POST /api/v1/extract` with `output_mode=BOTH`.
- **Steps:** Same steps 1–6 as SC-001, plus annotation from SC-002, plus multipart response assembly.
- **Expected Outcome:** `200 OK`, `Content-Type: multipart/mixed; boundary=<generated>`. Exactly two parts in order: `application/json` (`ExtractResponse`) and `application/pdf` (annotated PDF). Both parts carry `Content-Disposition` headers naming the parts.
- **Exceptions:** Same as SC-001.
- **Linked Requirements:** FR-001, FR-006, FR-031, FR-038, FR-045

### SC-004: Scanned PDF, OCR engaged (happy path)
- **Type:** happy-path
- **Persona:** P-01
- **Feature Area:** OCR path
- **Preconditions:** Scanned (image-based) PDF of ~5 pages; skill registered; Ollama reachable.
- **Trigger:** Any `output_mode`.
- **Steps:** Docling detects no text layer, engages its OCR pipeline according to `Settings.docling_ocr_default` or the skill's `docling:` override, produces `TextBlock`s with OCR-recovered text. Remaining pipeline identical to SC-001.
- **Expected Outcome:** `200 OK` with all fields extracted; higher latency than SC-001 (see NFR-005); per-field `status=extracted`, `grounded=true` (though sub-block matching may fall back to whole-block bboxes more often due to OCR character-offset drift).
- **Exceptions:** If OCR produces zero extractable text, see SC-020.
- **Linked Requirements:** FR-015, FR-019, FR-021

### SC-005: `skill_version=latest` resolves to highest registered integer
- **Type:** alternative
- **Persona:** P-01
- **Feature Area:** Skill versioning
- **Preconditions:** Skill `invoice` has versions `1`, `2`, `3` registered in `SkillManifest`.
- **Trigger:** `POST /api/v1/extract` with `skill_version=latest`.
- **Steps:** `SkillLoader.load("invoice", "latest")` resolves to `Skill(name="invoice", version=3, ...)`.
- **Expected Outcome:** `200 OK`; `ExtractResponse.skill_version == 3`.
- **Exceptions:**
  - If skill name exists but has zero registered versions (impossible in practice but covered by SC-015), return `SKILL_NOT_FOUND`.
- **Linked Requirements:** FR-011

### SC-006: Partial extraction — some fields fail validation after retries
- **Type:** alternative
- **Persona:** P-01
- **Feature Area:** Structured output hardening
- **Preconditions:** Skill requires 5 fields; Gemma 4 consistently produces invalid JSON for 1 field across 4 attempts while the other 4 fields succeed.
- **Trigger:** `POST /api/v1/extract` with `output_mode=JSON_ONLY`.
- **Steps:** Pipeline runs, `StructuredOutputValidator` retries the failing field 3 times, all retries fail, service marks that one field as failed and continues.
- **Expected Outcome:** `200 OK`; `ExtractResponse.fields` contains all 5 keys; 4 have `status=extracted` with values; 1 has `status=failed`, `value=null`, `grounded=false`, `bbox_refs=[]`. Metadata reports the per-field attempt counts.
- **Exceptions:**
  - If **all** 5 fields fail, see SC-026 (`STRUCTURED_OUTPUT_FAILED`).
- **Linked Requirements:** FR-024, FR-025, FR-026, FR-034, FR-036

### SC-007: Ungrounded (inferred) value with no source span
- **Type:** alternative
- **Persona:** P-01
- **Feature Area:** Grounding
- **Preconditions:** Skill declares a field `document_type`; the document does not mention a type explicitly but Gemma 4 infers it from general context.
- **Trigger:** Any `output_mode`.
- **Steps:** LangExtract returns the extracted value but marks it ungrounded (no source span). `SpanResolver` sees no valid offsets and returns the field with `source=inferred`, `grounded=false`, `bbox_refs=[]`.
- **Expected Outcome:** `200 OK`; the field is present with `status=extracted`, `value="<inferred>"`, `source=inferred`, `grounded=false`, `bbox_refs=[]`.
- **Exceptions:** None.
- **Linked Requirements:** FR-035, FR-037

### SC-008: Field value spans multiple blocks on the same page
- **Type:** edge-case
- **Persona:** P-01
- **Feature Area:** Coordinate matching
- **Preconditions:** Extracted value (e.g. a multi-word party name) starts in one Docling block and ends in the next block on the same page.
- **Trigger:** Any `output_mode`.
- **Steps:** `SpanResolver` uses `OffsetIndex.lookup` on both start and end offsets, detects they're in different blocks, and builds one `BoundingBoxRef` per touched block.
- **Expected Outcome:** Field's `bbox_refs` has multiple entries, all on the same `page`, covering each touched block.
- **Exceptions:** None.
- **Linked Requirements:** FR-032

### SC-009: Field value spans a page boundary
- **Type:** edge-case
- **Persona:** P-01
- **Feature Area:** Coordinate matching
- **Preconditions:** Extracted value is split across the bottom of page N and top of page N+1.
- **Trigger:** Any `output_mode`.
- **Steps:** Same as SC-008, but the touched blocks have different `page_number` values.
- **Expected Outcome:** `bbox_refs` has entries with different `page` values; `PdfAnnotator` draws highlights on both pages.
- **Exceptions:** None.
- **Linked Requirements:** FR-032, FR-038

### SC-010: Fully hallucinated value, no block contains it
- **Type:** edge-case
- **Persona:** P-01
- **Feature Area:** Coordinate matching
- **Preconditions:** Gemma 4 hallucinates a value that does not appear in any `TextBlock`.
- **Trigger:** Any `output_mode`.
- **Steps:** `SpanResolver` cannot locate the span in any block; `SubBlockMatcher` cannot find it either.
- **Expected Outcome:** Field present, `status=extracted`, `grounded=false`, `bbox_refs=[]`.
- **Exceptions:** None.
- **Linked Requirements:** FR-033

### SC-011: Sub-block match succeeds via whitespace normalization
- **Type:** edge-case
- **Persona:** P-01
- **Feature Area:** Coordinate matching
- **Preconditions:** Extracted value contains a single space but the Docling block text has a non-breaking space at the same position.
- **Trigger:** Any `output_mode`.
- **Steps:** `SubBlockMatcher` fails direct substring match, succeeds on whitespace-normalized match. Tight sub-block bbox is computed via character-offset ratios against the block's bbox.
- **Expected Outcome:** Field has `grounded=true`, `bbox_refs` contains one tight sub-block bbox (not the whole block).
- **Exceptions:**
  - If whitespace normalization also fails, NFKC normalization is tried next.
- **Linked Requirements:** FR-031

### SC-012: Sub-block match fails → whole-block fallback
- **Type:** edge-case
- **Persona:** P-01
- **Feature Area:** Coordinate matching
- **Preconditions:** Extracted value cannot be located inside the identified block via any of the three normalization steps (direct, whitespace, NFKC).
- **Trigger:** Any `output_mode`.
- **Steps:** `SubBlockMatcher.locate` returns `None`. `SpanResolver` falls back to the whole-block bbox.
- **Expected Outcome:** Field has `grounded=true`, `bbox_refs` contains the block-level bbox. Less visually precise but still correct at the block level.
- **Exceptions:**
  - If even the block cannot be identified (offset outside all blocks), see SC-010.
- **Linked Requirements:** FR-031

### SC-013: Unknown skill name
- **Type:** failure
- **Persona:** P-01
- **Feature Area:** Skill lookup
- **Preconditions:** No skill named `mystery` registered.
- **Trigger:** `POST /api/v1/extract` with `skill_name=mystery`, `skill_version=1`.
- **Steps:** `SkillLoader.load` raises `SkillNotFoundError`.
- **Expected Outcome:** `404 Not Found`, error body contains error code `SKILL_NOT_FOUND` and a non-localized machine-readable error response.
- **Exceptions:** None.
- **Linked Requirements:** FR-012

### SC-014: Known skill name, unknown version integer
- **Type:** failure
- **Persona:** P-01
- **Feature Area:** Skill lookup
- **Preconditions:** `invoice/1` exists; `invoice/99` does not.
- **Trigger:** `POST` with `skill_name=invoice`, `skill_version=99`.
- **Expected Outcome:** `404`, `SKILL_NOT_FOUND` (same code as SC-013; the service does not distinguish between "unknown name" and "unknown version" because neither is actionable to the caller beyond fixing the reference).
- **Linked Requirements:** FR-012

### SC-015: `skill_version=latest` against a skill name with zero registered versions
- **Type:** edge-case
- **Persona:** P-01
- **Feature Area:** Skill lookup
- **Preconditions:** No `invoice` skill at all.
- **Trigger:** `POST` with `skill_name=invoice`, `skill_version=latest`.
- **Expected Outcome:** `404`, `SKILL_NOT_FOUND`.
- **Linked Requirements:** FR-013

### SC-016: PDF byte size above limit
- **Type:** failure
- **Persona:** P-01
- **Feature Area:** Input validation
- **Preconditions:** `Settings.max_pdf_bytes = 50 MB`.
- **Trigger:** `POST` with a 60 MB PDF.
- **Steps:** Router measures upload size, raises `PdfTooLargeError` before calling any downstream component.
- **Expected Outcome:** `413 Payload Too Large`, error code `PDF_TOO_LARGE`. No parsing attempted.
- **Linked Requirements:** FR-002

### SC-017: PDF page count above limit
- **Type:** failure
- **Persona:** P-01
- **Feature Area:** Input validation
- **Preconditions:** `Settings.max_pdf_pages = 200`.
- **Trigger:** 250-page PDF, byte size under limit.
- **Steps:** `DoclingDocumentParser` opens the PDF, reads page count, raises `PdfTooManyPagesError` before OCR or layout analysis runs.
- **Expected Outcome:** `413`, error code `PDF_TOO_MANY_PAGES`.
- **Linked Requirements:** FR-003

### SC-018: Corrupted / invalid PDF
- **Type:** failure
- **Persona:** P-01
- **Feature Area:** Input validation
- **Preconditions:** File uploaded is not a valid PDF (text file with `.pdf` extension, truncated PDF, malformed header).
- **Trigger:** `POST` with invalid bytes.
- **Expected Outcome:** `400 Bad Request`, error code `PDF_INVALID`.
- **Linked Requirements:** FR-016

### SC-019: Password-protected PDF
- **Type:** failure
- **Persona:** P-01
- **Feature Area:** Input validation
- **Preconditions:** Uploaded PDF is encrypted.
- **Trigger:** `POST` with encrypted PDF.
- **Expected Outcome:** `400`, error code `PDF_PASSWORD_PROTECTED`.
- **Linked Requirements:** FR-017

### SC-020: PDF with no extractable text (even after OCR)
- **Type:** failure
- **Persona:** P-01
- **Feature Area:** Input validation
- **Preconditions:** PDF contains only blank pages or unreadable decorative images.
- **Trigger:** `POST` with such a PDF.
- **Steps:** Docling produces zero `TextBlock`s even after OCR; `DoclingDocumentParser` raises `PdfNoTextExtractableError`.
- **Expected Outcome:** `422 Unprocessable Entity`, error code `PDF_NO_TEXT_EXTRACTABLE`.
- **Linked Requirements:** FR-018

### SC-021: Missing required form field in request
- **Type:** failure
- **Persona:** P-01
- **Feature Area:** Request validation
- **Preconditions:** None.
- **Trigger:** `POST` without a `skill_name` form field.
- **Expected Outcome:** `422`, FastAPI native validation error response identifying the missing field.
- **Linked Requirements:** FR-008

### SC-022: Invalid `output_mode` enum value
- **Type:** failure
- **Persona:** P-01
- **Feature Area:** Request validation
- **Preconditions:** None.
- **Trigger:** `POST` with `output_mode=XML`.
- **Expected Outcome:** `422`, FastAPI native validation error naming the invalid field.
- **Linked Requirements:** FR-008

### SC-023: Ollama daemon unreachable
- **Type:** failure
- **Persona:** P-01
- **Feature Area:** Intelligence layer
- **Preconditions:** Ollama not running on the host.
- **Trigger:** `POST` valid request.
- **Steps:** `OllamaGemmaProvider.generate` attempts an HTTP call, gets connection refused, raises `IntelligenceUnavailableError`.
- **Expected Outcome:** `503 Service Unavailable`, error code `INTELLIGENCE_UNAVAILABLE`.
- **Linked Requirements:** FR-027

### SC-024: Ollama connection timeout
- **Type:** failure
- **Persona:** P-01
- **Feature Area:** Intelligence layer
- **Preconditions:** Ollama slow or stuck; connect timeout exceeds `Settings.ollama_timeout_seconds`.
- **Trigger:** `POST` valid request.
- **Expected Outcome:** `503`, error code `INTELLIGENCE_UNAVAILABLE` (same code as SC-023; the distinction is in logs, not the HTTP response).
- **Linked Requirements:** FR-027

### SC-025: End-to-end extraction exceeds timeout budget
- **Type:** failure
- **Persona:** P-01
- **Feature Area:** Request lifecycle
- **Preconditions:** Large scanned PDF at the edge of page limit; heavy OCR and many chunks.
- **Trigger:** `POST` valid request.
- **Steps:** `asyncio.timeout(Settings.extraction_timeout_seconds)` fires mid-pipeline; `ExtractionService` catches, raises `IntelligenceTimeoutError`.
- **Expected Outcome:** `504 Gateway Timeout`, error code `INTELLIGENCE_TIMEOUT`.
- **Linked Requirements:** FR-007

### SC-026: Structured output fails across all fields after 4 attempts
- **Type:** failure
- **Persona:** P-01
- **Feature Area:** Structured output hardening
- **Preconditions:** Skill prompt is poorly authored for the document type, Gemma 4 cannot produce a valid structured response for any declared field across 4 attempts.
- **Trigger:** `POST` valid request.
- **Expected Outcome:** `502 Bad Gateway`, error code `STRUCTURED_OUTPUT_FAILED`. Partial success returns 200 (see SC-006); this is the "no fields succeeded" branch.
- **Linked Requirements:** FR-026

### SC-027: Startup with all skill YAMLs valid
- **Type:** happy-path (startup)
- **Persona:** P-01
- **Feature Area:** Skill manifest startup validation
- **Preconditions:** `skills_dir` contains well-formed YAMLs under `skills/{name}/{version}.yaml`, each example matches its declared `output_schema`, each filename version matches the body `version`.
- **Trigger:** Container boot.
- **Steps:** `SkillLoader` walks the directory, validates each YAML via `SkillYamlSchema`, registers in `SkillManifest`.
- **Expected Outcome:** Container reaches ready state; `/ready` returns 200 once Ollama probe also succeeds.
- **Linked Requirements:** FR-009, FR-042

### SC-028: Startup with malformed skill YAML
- **Type:** failure (startup)
- **Persona:** P-01
- **Feature Area:** Skill manifest startup validation
- **Preconditions:** One YAML file under `skills_dir` is malformed (YAML syntax error, schema mismatch, or example fails against declared schema).
- **Trigger:** Container boot.
- **Steps:** `SkillLoader` raises `SkillValidationError` with the offending filename and reason.
- **Expected Outcome:** Container logs `SKILL_VALIDATION_FAILED` to stderr, exits non-zero. No `/ready` green state ever reached.
- **Linked Requirements:** FR-010

### SC-029: Startup with Ollama unreachable — degraded mode
- **Type:** failure (startup)
- **Persona:** P-01
- **Feature Area:** Readiness / degraded mode
- **Preconditions:** Skill YAMLs all valid, Ollama not running at boot time.
- **Trigger:** Container boot.
- **Steps:** Container starts successfully. `SkillManifest` is populated. `/health` returns 200. Ollama probe fails; `/ready` returns 503. Any incoming request returns `INTELLIGENCE_UNAVAILABLE`. When Ollama becomes reachable, the next probe succeeds (within the configured TTL) and `/ready` flips to 200.
- **Expected Outcome:** Service self-heals once Ollama becomes available; no restart required.
- **Linked Requirements:** FR-041, FR-042, FR-043

### SC-030: Malformed or malicious PDF (zip-bomb style)
- **Type:** abuse
- **Persona:** P-01
- **Feature Area:** Input validation + robustness
- **Preconditions:** PDF crafted to exercise Docling's parser pathologically.
- **Trigger:** `POST` with malicious PDF.
- **Expected Outcome:** Either `400 PDF_INVALID` (if Docling raises cleanly) or `413 PDF_TOO_LARGE` (if the byte prefilter catches it first). No memory exhaustion beyond the 2.5 GB ceiling, no filesystem writes, no infinite loop — though the service trusts Docling's own safeguards here and does not add a memory-limit sandbox in v1.
- **Exceptions:**
  - If Docling itself hangs (no raise, no progress), the end-to-end timeout in SC-025 catches it at the 180 s ceiling.
- **Linked Requirements:** FR-002, FR-016, NFR-010

### SC-031: Concurrent requests against `workers=1`
- **Type:** edge-case
- **Persona:** P-01
- **Feature Area:** Concurrency
- **Preconditions:** Two HTTP clients send requests within milliseconds of each other.
- **Trigger:** Two `POST`s arriving simultaneously.
- **Steps:** FastAPI/Uvicorn queues the second request in the event loop. The first request processes to completion, then the second processes. Both share the read-only `SkillManifest` with no mutation, so no race.
- **Expected Outcome:** Both requests complete successfully with correct responses. Second request observes higher latency (first request's duration + its own).
- **Linked Requirements:** FR-044

### SC-032: Skill applied to wrong document type (semantic mismatch)
- **Type:** edge-case
- **Persona:** P-01
- **Feature Area:** Error surface
- **Preconditions:** Caller passes an invoice skill against a research paper (domains don't match).
- **Trigger:** `POST` valid request.
- **Expected Outcome:** `200 OK`. Every field declared by the skill is present in the response; most will have `status=failed` (structured output failed to extract meaningful values) or `grounded=false` (values produced but not found in the document). The service does not detect semantic mismatch itself — the caller is responsible for validating the response matches their expectations.
- **Exceptions:** None.
- **Linked Requirements:** FR-034, FR-036

---

## 5. Functional Requirements

Every `Must` FR carries Given/When/Then acceptance criteria. FRs flagged `(architectural)` are enforced by `import-linter` contracts (Section 10 of the design spec) rather than by behavioral scenarios.

### FR-001: Extraction endpoint — request shape
- **Priority:** Must
- **Statement:** The system shall expose `POST /api/v1/extract` accepting a `multipart/form-data` request with fields `pdf` (UploadFile), `skill_name` (string), `skill_version` (string: positive integer or `latest`), and `output_mode` (enum: `JSON_ONLY` / `PDF_ONLY` / `BOTH`).
- **Rationale:** Defines the single entry point for extraction; everything downstream depends on a stable request contract.
- **Stakeholder(s):** S-01
- **Persona(s):** P-01
- **Linked Scenario(s):** SC-001, SC-002, SC-003
- **Dependencies:** None
- **Acceptance Criteria:**
  - **Given** a well-formed multipart request with all four fields
  - **When** the request hits `POST /api/v1/extract`
  - **Then** the request is parsed into an `ExtractRequest` object and passed to `ExtractionService`
  - **And** the PDF bytes are read from the `UploadFile` into memory
- **Test Type:** integration

### FR-002: Max PDF byte size guard
- **Priority:** Must
- **Statement:** The system shall reject requests whose PDF byte size exceeds `Settings.max_pdf_bytes` with HTTP 413 `PDF_TOO_LARGE`, evaluated in the router before any parsing work is allocated.
- **Rationale:** Prevents memory exhaustion from oversized uploads; a hard prefilter is cheaper than catching it later.
- **Linked Scenario(s):** SC-016, SC-030
- **Acceptance Criteria:**
  - **Given** `Settings.max_pdf_bytes = 50 * 1024 * 1024`
  - **When** a request uploads a 50 MB + 1 byte PDF
  - **Then** the response is HTTP 413 with error code `PDF_TOO_LARGE`
  - **And** no `DoclingDocumentParser` invocation occurs
- **Test Type:** integration

### FR-003: Max PDF page count guard
- **Priority:** Must
- **Statement:** The system shall reject PDFs whose page count exceeds `Settings.max_pdf_pages` with HTTP 413 `PDF_TOO_MANY_PAGES`, evaluated inside `DoclingDocumentParser` as soon as page count is known and before OCR or layout analysis runs.
- **Rationale:** Page count is a better proxy for extraction cost than byte size; a 40 MB image-heavy 10-page PDF is cheaper than a 2 MB 500-page dense text PDF.
- **Linked Scenario(s):** SC-017
- **Acceptance Criteria:**
  - **Given** `Settings.max_pdf_pages = 200`
  - **When** a request uploads a 201-page PDF
  - **Then** the response is HTTP 413 with error code `PDF_TOO_MANY_PAGES`
  - **And** no OCR or layout analysis runs
- **Test Type:** integration

### FR-004: JSON-only response serialization
- **Priority:** Must
- **Statement:** The system shall serialize `JSON_ONLY` responses as `Content-Type: application/json`, body an `ExtractResponse`.
- **Linked Scenario(s):** SC-001
- **Acceptance Criteria:**
  - **Given** `output_mode=JSON_ONLY` and a successful extraction
  - **When** the service returns
  - **Then** the HTTP response is 200, `Content-Type: application/json`
  - **And** the body conforms to the `ExtractResponse` Pydantic schema
- **Test Type:** integration

### FR-005: PDF-only response serialization
- **Priority:** Must
- **Statement:** The system shall serialize `PDF_ONLY` responses as `Content-Type: application/pdf`, body the annotated PDF bytes, with no JSON envelope.
- **Linked Scenario(s):** SC-002
- **Acceptance Criteria:**
  - **Given** `output_mode=PDF_ONLY` and a successful extraction
  - **When** the service returns
  - **Then** the HTTP response is 200, `Content-Type: application/pdf`
  - **And** the body is a valid PDF parseable by PyMuPDF
- **Test Type:** integration

### FR-006: Multipart `BOTH` response serialization
- **Priority:** Must
- **Statement:** The system shall serialize `BOTH` responses as `Content-Type: multipart/mixed; boundary=<generated>`, with exactly two parts in order: part 1 is `application/json` (`ExtractResponse`), part 2 is `application/pdf` (annotated PDF). Each part carries `Content-Disposition` identifying its name.
- **Linked Scenario(s):** SC-003
- **Acceptance Criteria:**
  - **Given** `output_mode=BOTH` and a successful extraction
  - **When** the service returns
  - **Then** the HTTP response is 200, `Content-Type: multipart/mixed` with a boundary
  - **And** parsing the body as multipart yields exactly two parts
  - **And** part 1 is `application/json` and validates as `ExtractResponse`
  - **And** part 2 is `application/pdf`
- **Test Type:** integration

### FR-007: End-to-end timeout enforcement
- **Priority:** Must
- **Statement:** The system shall enforce an end-to-end timeout equal to `Settings.extraction_timeout_seconds` (default 180 s) across parsing, extraction, retries, and annotation combined, returning HTTP 504 `INTELLIGENCE_TIMEOUT` on exceed.
- **Linked Scenario(s):** SC-025
- **Acceptance Criteria:**
  - **Given** `Settings.extraction_timeout_seconds = 5` and a stub provider that sleeps 10 s
  - **When** a request is made
  - **Then** the response is HTTP 504 `INTELLIGENCE_TIMEOUT`
  - **And** the request completes within roughly 5 s wall-clock
- **Test Type:** integration

### FR-008: Request validation for malformed input
- **Priority:** Must
- **Statement:** The system shall reject malformed requests (missing required fields, invalid enum values, wrong content types) with HTTP 422 via FastAPI's native validation layer.
- **Linked Scenario(s):** SC-021, SC-022
- **Acceptance Criteria:**
  - **Given** a request missing the `skill_name` field
  - **When** the request arrives at `/api/v1/extract`
  - **Then** the response is 422 with a field-level validation error
- **Test Type:** integration

### FR-009: Startup — skill YAML loading and validation
- **Priority:** Must
- **Statement:** At container startup, the system shall load every YAML file under `Settings.skills_dir`, validate each against `SkillYamlSchema` (required fields present, `output_schema` is a valid JSONSchema, every example's `output` validates against `output_schema`, filename version matches body `version`), and register valid skills in an in-memory `SkillManifest` keyed by `(name, version)`.
- **Linked Scenario(s):** SC-027
- **Acceptance Criteria:**
  - **Given** a `skills_dir` with two valid YAMLs and one invalid YAML
  - **When** the service initializes
  - **Then** `SkillManifest` rejects the whole startup (FR-010) — no partial state
  - **Given** a `skills_dir` with three valid YAMLs
  - **When** the service initializes
  - **Then** `SkillManifest` contains all three, keyed by `(name, version)`
- **Test Type:** unit

### FR-010: Startup failure on malformed skill
- **Priority:** Must
- **Statement:** If any skill YAML fails startup validation, the system shall log `SKILL_VALIDATION_FAILED` to stderr with the offending filename and reason and exit the process with a non-zero status code. The container shall not reach a ready state.
- **Linked Scenario(s):** SC-028
- **Acceptance Criteria:**
  - **Given** a `skills_dir` containing one malformed YAML
  - **When** the service is started
  - **Then** the process exits with status code != 0 within the cold-start window
  - **And** stderr contains a `SKILL_VALIDATION_FAILED` entry naming the file
- **Test Type:** unit

### FR-011: `latest` version resolution
- **Priority:** Must
- **Statement:** The system shall resolve `skill_version=latest` at request time to the highest integer version registered for the requested skill name.
- **Linked Scenario(s):** SC-005
- **Acceptance Criteria:**
  - **Given** skill `invoice` registered at versions 1, 2, 3
  - **When** a request uses `skill_version=latest`
  - **Then** the service uses version 3
  - **And** the response's `skill_version` field is the integer `3`, not the string `latest`
- **Test Type:** unit

### FR-012: Integer version lookup; unknown `(name, version)`
- **Priority:** Must
- **Statement:** The system shall resolve `skill_version=<integer>` to the exact registered version, or raise `SKILL_NOT_FOUND` (404) if no such `(name, version)` pair exists. The same error code is used whether the name is unknown or the version is unknown.
- **Linked Scenario(s):** SC-013, SC-014
- **Acceptance Criteria:**
  - **Given** no skill named `mystery` in the manifest
  - **When** a request uses `skill_name=mystery, skill_version=1`
  - **Then** the response is 404 with error code `SKILL_NOT_FOUND`
- **Test Type:** integration

### FR-013: `latest` against skill with zero versions
- **Priority:** Must
- **Statement:** The system shall raise `SKILL_NOT_FOUND` (404) for `skill_version=latest` requests against a skill name with zero registered versions (which implies the name itself is unknown).
- **Linked Scenario(s):** SC-015
- **Acceptance Criteria:**
  - **Given** no skill named `invoice` in the manifest
  - **When** a request uses `skill_name=invoice, skill_version=latest`
  - **Then** the response is 404 with error code `SKILL_NOT_FOUND`
- **Test Type:** integration

### FR-014: Native digital PDF parsing
- **Priority:** Must
- **Statement:** The system shall parse native digital PDFs (those with a text layer) via Docling and emit a `ParsedDocument` containing a list of `TextBlock` instances with fields `text`, `page_number` (1-indexed), `bbox` (PDF page coordinates with origin bottom-left), and `block_id` (stable within a single parse run).
- **Linked Scenario(s):** SC-001
- **Acceptance Criteria:**
  - **Given** a native digital 3-page PDF fixture
  - **When** `DoclingDocumentParser.parse` is invoked
  - **Then** the returned `ParsedDocument` contains at least one `TextBlock` per page
  - **And** every `TextBlock` has non-empty `text` and a valid `BoundingBox`
- **Test Type:** integration (real Docling)

### FR-015: Scanned PDF parsing via OCR
- **Priority:** Must
- **Statement:** The system shall parse scanned PDFs (image-based, no text layer) via Docling's OCR pipeline, producing the same `ParsedDocument` shape as native PDFs.
- **Linked Scenario(s):** SC-004
- **Acceptance Criteria:**
  - **Given** a scanned 2-page PDF fixture
  - **When** `DoclingDocumentParser.parse` is invoked
  - **Then** the returned `ParsedDocument` contains OCR-recovered text blocks
  - **And** each block has plausible (if imprecise) bounding boxes
- **Test Type:** integration (real Docling)

### FR-016: Invalid PDF handling
- **Priority:** Must
- **Statement:** The system shall raise `PDF_INVALID` (400) when the uploaded bytes are not a readable PDF (corrupted, truncated, wrong content type, or unparseable by Docling).
- **Linked Scenario(s):** SC-018, SC-030
- **Acceptance Criteria:**
  - **Given** a file whose bytes are `"not a pdf"`
  - **When** a request is made
  - **Then** the response is 400 with error code `PDF_INVALID`
- **Test Type:** integration

### FR-017: Password-protected PDF handling
- **Priority:** Must
- **Statement:** The system shall raise `PDF_PASSWORD_PROTECTED` (400) when the PDF is encrypted.
- **Linked Scenario(s):** SC-019
- **Acceptance Criteria:**
  - **Given** an encrypted PDF fixture
  - **When** a request is made
  - **Then** the response is 400 with error code `PDF_PASSWORD_PROTECTED`
- **Test Type:** integration

### FR-018: No-extractable-text handling
- **Priority:** Must
- **Statement:** The system shall raise `PDF_NO_TEXT_EXTRACTABLE` (422) when the PDF yields zero text blocks after both native parsing and OCR attempts.
- **Linked Scenario(s):** SC-020
- **Acceptance Criteria:**
  - **Given** a PDF containing only a blank page
  - **When** a request is made
  - **Then** the response is 422 with error code `PDF_NO_TEXT_EXTRACTABLE`
- **Test Type:** integration

### FR-019: Per-skill Docling configuration override
- **Priority:** Must
- **Statement:** The system shall apply a skill YAML's `docling:` configuration section when present, merging it over the global defaults from `Settings.docling_*`.
- **Linked Scenario(s):** SC-004
- **Acceptance Criteria:**
  - **Given** a skill with `docling: {ocr: force, table_mode: accurate}` and global defaults `{ocr: auto, table_mode: fast}`
  - **When** `DoclingDocumentParser` is invoked for that skill
  - **Then** the effective config passed to Docling is `{ocr: force, table_mode: accurate}`
  - **And** other unset keys fall back to global defaults
- **Test Type:** unit

### FR-020: Docling dependency containment (architectural)
- **Priority:** Must
- **Statement:** The system shall confine all imports of the Docling library to the `DoclingDocumentParser` implementation file. No other module may import Docling types directly.
- **Linked Scenario(s):** N/A (architectural)
- **Acceptance Criteria:**
  - **Given** the `import-linter` configuration file
  - **When** `task lint` runs
  - **Then** the contract `docling-containment` passes
  - **And** any new import of `docling` outside the allowed file fails the lint step
- **Test Type:** `import-linter` contract

### FR-021: LangExtract orchestration
- **Priority:** Must
- **Statement:** The system shall invoke LangExtract with the skill's `prompt`, `examples`, and `output_schema` as parameters, routing every model call through an `IntelligenceProvider` implementation.
- **Linked Scenario(s):** SC-001
- **Acceptance Criteria:**
  - **Given** a valid skill and concatenated document text
  - **When** `ExtractionEngine.extract` is invoked
  - **Then** LangExtract is called with the skill's prompt/examples/schema
  - **And** every model call within LangExtract's chunking loop is handled by the injected `IntelligenceProvider`
- **Test Type:** unit (mock provider)

### FR-022: `OllamaGemmaProvider` dual-interface
- **Priority:** Must
- **Statement:** The system shall ship an `OllamaGemmaProvider` class that simultaneously satisfies the internal `IntelligenceProvider` protocol and LangExtract's community provider plugin contract. The provider shall hold a single Ollama HTTP client configured by `Settings.ollama_base_url`, `Settings.ollama_model`, and `Settings.ollama_timeout_seconds`.
- **Linked Scenario(s):** SC-001
- **Acceptance Criteria:**
  - **Given** the service at runtime
  - **When** LangExtract's provider discovery mechanism looks up providers
  - **Then** `OllamaGemmaProvider` is registered and selected
  - **And** the same class instance can also be invoked through the `IntelligenceProvider.generate` protocol method
- **Test Type:** integration

### FR-023: Default model is smallest Gemma 4 variant
- **Priority:** Must
- **Statement:** The default `Settings.ollama_model` value shall be the smallest Gemma 4 variant available via Ollama. The model tag shall never be hardcoded in source; it is always read from configuration, making substitution a config change rather than a code change.
- **Linked Scenario(s):** SC-001
- **Acceptance Criteria:**
  - **Given** the service running with no `OLLAMA_MODEL` env var set
  - **When** `Settings` is loaded
  - **Then** `settings.ollama_model` returns a Gemma-4-family tag representing the smallest variant
  - **And** grep of the source tree finds no hardcoded Gemma model string outside `config.py` and `.env.example`
- **Test Type:** unit + code search

### FR-024: Structured output parsing and validation
- **Priority:** Must
- **Statement:** The system shall delegate structured output parsing, cleanup, and validation to `StructuredOutputValidator`, which strips markdown code fences, extracts the first JSON object from prose wrappers, parses via `json.loads`, and validates via `jsonschema.validate` against the provided output schema.
- **Linked Scenario(s):** SC-006, SC-026
- **Acceptance Criteria:**
  - **Given** raw model output `"```json\n{\"foo\": \"bar\"}\n```"` and a schema requiring `foo: string`
  - **When** `StructuredOutputValidator.validate_and_retry` is called
  - **Then** the returned `GenerationResult.data` is `{"foo": "bar"}`
  - **And** `attempts` is 1 (no retry needed)
- **Test Type:** unit

### FR-025: Structured output retry loop
- **Priority:** Must
- **Statement:** The system shall retry structured output generation up to 3 additional times (4 total attempts) on any parse or validation failure, invoking a correction prompt built by `CorrectionPromptBuilder` that includes the malformed output and the expected schema.
- **Linked Scenario(s):** SC-006, SC-026
- **Acceptance Criteria:**
  - **Given** a mock regeneration callable that returns invalid JSON 3 times then valid JSON on the 4th attempt
  - **When** `StructuredOutputValidator.validate_and_retry` is called
  - **Then** the returned `GenerationResult.attempts` is 4
  - **And** the returned `data` is the final valid parse
  - **Given** the same mock returning invalid JSON on all 4 attempts
  - **When** validation is called
  - **Then** `StructuredOutputError` is raised with the last raw output attached
- **Test Type:** unit

### FR-026: Total structured output failure — HTTP 502
- **Priority:** Must
- **Statement:** The system shall raise `STRUCTURED_OUTPUT_FAILED` (502) only when every declared field has exhausted its retry budget without producing a valid value. Partial success (at least one field valid) returns 200 with per-field status.
- **Linked Scenario(s):** SC-026
- **Acceptance Criteria:**
  - **Given** a skill with 3 fields and a stub provider that always fails
  - **When** a request is made
  - **Then** the response is 502 `STRUCTURED_OUTPUT_FAILED`
  - **Given** the same skill with a stub provider that succeeds on 1 of 3 fields
  - **When** a request is made
  - **Then** the response is 200 with the successful field marked `status=extracted` and the other two marked `status=failed`
- **Test Type:** integration

### FR-027: Ollama unreachable — HTTP 503
- **Priority:** Must
- **Statement:** The system shall raise `INTELLIGENCE_UNAVAILABLE` (503) when the Ollama daemon is unreachable, refuses the connection, or times out on connect per `Settings.ollama_timeout_seconds`.
- **Linked Scenario(s):** SC-023, SC-024
- **Acceptance Criteria:**
  - **Given** `Settings.ollama_base_url` pointing to a closed port
  - **When** a request is made
  - **Then** the response is 503 `INTELLIGENCE_UNAVAILABLE` within `ollama_timeout_seconds` + small slack
- **Test Type:** integration

### FR-028: Ollama client dependency containment (architectural)
- **Priority:** Must
- **Statement:** The system shall confine all imports of the Ollama HTTP client library to the `OllamaGemmaProvider` implementation file.
- **Linked Scenario(s):** N/A (architectural)
- **Acceptance Criteria:** `import-linter` contract `ollama-client-containment` passes in `task lint`.
- **Test Type:** `import-linter`

### FR-029: LangExtract dependency containment (architectural)
- **Priority:** Must
- **Statement:** The system shall confine all imports of the LangExtract library to the `ExtractionEngine` implementation file and the LangExtract provider plugin registration point.
- **Linked Scenario(s):** N/A (architectural)
- **Acceptance Criteria:** `import-linter` contract `langextract-containment` passes in `task lint`.
- **Test Type:** `import-linter`

### FR-030: Text concatenation and offset index
- **Priority:** Must
- **Statement:** The system shall concatenate `ParsedDocument` blocks into a single text string using a configurable separator (default `"\n\n"`), producing a companion `OffsetIndex` that maps each concatenated-text character offset back to its source block and within-block offset.
- **Linked Scenario(s):** SC-001
- **Acceptance Criteria:**
  - **Given** a `ParsedDocument` with three fake `TextBlock`s: `"hello"`, `"world"`, `"foo"`
  - **When** `TextConcatenator.concatenate` is invoked with separator `"\n\n"`
  - **Then** the concatenated text is `"hello\n\nworld\n\nfoo"`
  - **And** `OffsetIndex.lookup(0)` returns the first block
  - **And** `OffsetIndex.lookup(7)` returns the second block with within-block offset 0
- **Test Type:** unit

### FR-031: Sub-block word matching with fallback chain
- **Priority:** Must
- **Statement:** For spans contained within a single block, the system shall invoke `SubBlockMatcher.locate` which attempts substring match in this order: (1) direct substring, (2) whitespace-normalized, (3) NFKC-normalized. On success, a tight sub-block bounding box is computed via character-offset ratios against the block's bbox. On failure, the whole-block bbox is returned.
- **Linked Scenario(s):** SC-011, SC-012
- **Acceptance Criteria:**
  - **Given** a block text `"Total: $1,847.50 due"` and an extracted value `"$1,847.50"`
  - **When** `SubBlockMatcher.locate` is called
  - **Then** it returns a `CharRange(start=7, end=16)`
  - **And** `SpanResolver` computes a sub-block bbox whose horizontal extent is roughly 7/20 to 16/20 of the block's width
  - **Given** a block text `"Total:\u00a0$1,847.50"` (non-breaking space) and an extracted value `"Total: $1,847.50"` (regular space)
  - **When** `SubBlockMatcher.locate` is called
  - **Then** it succeeds via the whitespace-normalization branch
- **Test Type:** unit

### FR-032: Multi-block span resolution
- **Priority:** Must
- **Statement:** For spans crossing multiple blocks (within or across pages), the system shall return one `BoundingBoxRef` per block the span touches, preserving page boundaries naturally in the list.
- **Linked Scenario(s):** SC-008, SC-009
- **Acceptance Criteria:**
  - **Given** a `RawExtraction` with offsets spanning blocks 2 and 3 in the `OffsetIndex`
  - **When** `SpanResolver.resolve` is called
  - **Then** the returned `ExtractedField.bbox_refs` has exactly 2 entries, one per touched block
- **Test Type:** unit

### FR-033: Ungrounded / hallucinated value handling
- **Priority:** Must
- **Statement:** When no block contains the raw extraction's offsets, the system shall return the field with `grounded=false` and `bbox_refs=[]`. The field's `value` and `status` are unaffected — only the grounding metadata reflects the miss.
- **Linked Scenario(s):** SC-010
- **Acceptance Criteria:**
  - **Given** a `RawExtraction` with `char_offset_start=-1` (LangExtract marker for "no span")
  - **When** `SpanResolver.resolve` is called
  - **Then** the returned `ExtractedField` has `grounded=false, bbox_refs=[]`
  - **And** if the raw extraction had `grounded=false` on the LangExtract side too, the field's `source=inferred`
- **Test Type:** unit

### FR-034: Complete field presence invariant
- **Priority:** Must
- **Statement:** Every field declared in the skill's `output_schema.properties` shall appear in the response's `fields` dictionary, regardless of extraction success. Missing keys are never silently dropped; failed fields are explicit.
- **Linked Scenario(s):** SC-006, SC-032
- **Acceptance Criteria:**
  - **Given** a skill declaring 5 required fields and a stub provider that returns values for only 3 of them
  - **When** a request is made
  - **Then** `ExtractResponse.fields` has exactly 5 keys
  - **And** the 2 missing fields have `status=failed, value=null`
- **Test Type:** integration

### FR-035: `ExtractedField` shape
- **Priority:** Must
- **Statement:** Each `ExtractedField` in the response shall include: `name` (str), `value` (Any | None), `status` (`extracted` | `failed`), `source` (`document` | `inferred`), `grounded` (bool), `bbox_refs` (list of `BoundingBoxRef`, possibly empty).
- **Linked Scenario(s):** SC-006, SC-007, SC-010
- **Acceptance Criteria:**
  - **Given** any successful extraction
  - **When** the response is parsed
  - **Then** every field in `fields` validates against the `ExtractedField` Pydantic schema
- **Test Type:** integration + schema contract

### FR-036: Failed field sentinel shape
- **Priority:** Must
- **Statement:** Fields whose extraction failed after all retries shall have `status="failed"`, `value=null`, `grounded=false`, `bbox_refs=[]`.
- **Linked Scenario(s):** SC-006
- **Acceptance Criteria:**
  - **Given** a stub provider that always fails for one specific field
  - **When** a request is made
  - **Then** that field's `status` is `failed`, `value` is `null`, `grounded` is `false`, `bbox_refs` is an empty list
- **Test Type:** integration

### FR-037: Ungrounded source classification
- **Priority:** Must
- **Statement:** Ungrounded extractions (LangExtract produced a value but marked it as not sourced from document text) shall have `source=inferred`. The `grounded` flag is set independently by coordinate matching — most ungrounded values will also have `grounded=false`, but the two flags are not identical.
- **Linked Scenario(s):** SC-007
- **Acceptance Criteria:**
  - **Given** a `RawExtraction` with `grounded=False` on the LangExtract side
  - **When** `SpanResolver.resolve` processes it
  - **Then** the resulting `ExtractedField.source = "inferred"`
- **Test Type:** unit

### FR-038: PDF annotation
- **Priority:** Must
- **Statement:** For `output_mode in {PDF_ONLY, BOTH}`, the system shall annotate the original PDF via PyMuPDF, drawing a highlight annotation on the source page at each `BoundingBoxRef`'s coordinates. Fields with empty `bbox_refs` are silently skipped (no error).
- **Linked Scenario(s):** SC-002, SC-003, SC-009
- **Acceptance Criteria:**
  - **Given** a PDF, a list of `ExtractedField`s with known `bbox_refs`
  - **When** `PdfAnnotator.annotate` is called
  - **Then** the returned bytes are a valid PDF
  - **And** the returned PDF contains highlight annotations at the specified coordinates on the specified pages
  - **And** fields with empty `bbox_refs` contribute no annotations
- **Test Type:** integration (real PyMuPDF round-trip)

### FR-039: Coordinate matching runs for all output modes
- **Priority:** Must
- **Statement:** Coordinate matching (`TextConcatenator` → `OffsetIndex` → `SpanResolver`) shall run for every request regardless of `output_mode`. Only the serialization layer differs across modes: `JSON_ONLY` skips `PdfAnnotator`; `PDF_ONLY` skips JSON serialization; `BOTH` runs everything. This correction supersedes the design spec's Section 5.2 wording, which ambiguously suggested coordinate matching could be skipped for `PDF_ONLY`.
- **Linked Scenario(s):** SC-001, SC-002
- **Acceptance Criteria:**
  - **Given** `output_mode=PDF_ONLY` and a stub provider that records whether `SpanResolver` was invoked
  - **When** a request is made
  - **Then** `SpanResolver` was invoked
  - **Given** `output_mode=JSON_ONLY`
  - **When** a request is made
  - **Then** `PdfAnnotator` was not invoked
- **Test Type:** unit

### FR-040: PyMuPDF dependency containment (architectural)
- **Priority:** Must
- **Statement:** The system shall confine all imports of PyMuPDF (`fitz`) to `PdfAnnotator` (and, if necessary for password-detection preflight, `DoclingDocumentParser`).
- **Linked Scenario(s):** N/A (architectural)
- **Acceptance Criteria:** `import-linter` contract `pymupdf-containment` passes in `task lint`.
- **Test Type:** `import-linter`

### FR-041: Liveness endpoint `/health`
- **Priority:** Must
- **Statement:** The system shall expose `GET /health` returning `200 OK` whenever the process is running and capable of serving requests. `/health` reflects liveness only — it does not probe Ollama.
- **Linked Scenario(s):** SC-029
- **Acceptance Criteria:**
  - **Given** the service is running and Ollama is unreachable
  - **When** a request is made to `/health`
  - **Then** the response is `200 OK`
- **Test Type:** integration

### FR-042: Readiness endpoint `/ready`
- **Priority:** Must
- **Statement:** The system shall expose `GET /ready` returning `200 OK` only when a recent Ollama probe (within a configurable TTL, default 10 s) has succeeded, and `503 Service Unavailable` otherwise. Readiness reflects dependency availability.
- **Linked Scenario(s):** SC-029
- **Acceptance Criteria:**
  - **Given** the service is running and Ollama is reachable, and a probe has succeeded within the TTL window
  - **When** a request is made to `/ready`
  - **Then** the response is `200 OK`
  - **Given** the same setup but Ollama is now down and the TTL has expired
  - **When** a request is made to `/ready`
  - **Then** the response is `503`
- **Test Type:** integration

### FR-043: Degraded-mode startup
- **Priority:** Must
- **Statement:** The system shall start successfully even when Ollama is unreachable at boot. In this state, `/health` returns 200, `/ready` returns 503, and extraction requests return 503 `INTELLIGENCE_UNAVAILABLE`. When Ollama becomes reachable, the next successful probe flips `/ready` to 200 and extraction requests succeed, without any restart.
- **Linked Scenario(s):** SC-029
- **Acceptance Criteria:**
  - **Given** Ollama is unreachable and `SkillManifest` is valid
  - **When** the service is started
  - **Then** the process does not exit and `/health` is 200
  - **And** `/ready` is 503
  - **When** Ollama becomes reachable
  - **Then** within `probe_ttl + probe_interval`, `/ready` flips to 200
- **Test Type:** integration

### FR-044: Single-worker serialized concurrency
- **Priority:** Must
- **Statement:** The system shall run with Uvicorn `workers=1` by default. Concurrent incoming requests are queued by FastAPI's event loop and served serially without race conditions on the read-only `SkillManifest`.
- **Linked Scenario(s):** SC-031
- **Acceptance Criteria:**
  - **Given** the service running with `workers=1`
  - **When** two valid requests are fired concurrently via two HTTP clients
  - **Then** both requests succeed with correct responses
  - **And** the second request's total latency is at least the first request's duration
- **Test Type:** integration

### FR-045: Structured request logging
- **Priority:** Must
- **Statement:** The system shall emit structured logs via `structlog` for every request, including fields `request_id`, `skill_name`, `skill_version`, `output_mode`, `duration_ms`, `outcome` (success / error code), and per-field attempt counts. Logs shall not contain raw PDF bytes, raw extracted field values, or full model prompts.
- **Linked Scenario(s):** All (operational)
- **Acceptance Criteria:**
  - **Given** a successful extraction request
  - **When** logs are captured
  - **Then** exactly one log entry of type `extraction_completed` is emitted for that request
  - **And** the entry contains all required fields
  - **And** the entry does not contain the literal text of any extracted field value
- **Test Type:** unit + log fixture inspection

### Won't-haves (v1 non-requirements)

| ID | Statement |
|---|---|
| **FR-W01** | Authentication / authorization on the `/extract` endpoint. |
| **FR-W02** | Persistent storage of PDFs, extractions, or request history. |
| **FR-W03** | Async job queue; request handling is synchronous. |
| **FR-W04** | Streaming responses for large PDFs. |
| **FR-W05** | Multi-tenant isolation. |
| **FR-W06** | Rate limiting at the microservice level. |
| **FR-W07** | Skill hot-reload without container restart. |
| **FR-W08** | A second `IntelligenceProvider` implementation (Claude, GPT-4, other). |

### Requirements Summary

| Priority | Count |
|---|---|
| Must | 45 |
| Should | 0 |
| Could | 0 |
| Won't | 8 |
| **Total** | **53** |

---

## 6. Non-Functional Requirements

| ID | Category | Requirement | Threshold | Measurement Method | Priority |
|---|---|---|---|---|---|
| **NFR-001** | Performance — hard limit | Max accepted PDF byte size | `50 MB` | Integration test asserts HTTP 413 at 50 MB + 1 byte | Must |
| **NFR-002** | Performance — hard limit | Max accepted PDF page count | `200` | Integration test asserts HTTP 413 at 201 pages | Must |
| **NFR-003** | Performance — hard limit | End-to-end request timeout | `180 s` wall-clock | Integration test with slow mock provider asserts HTTP 504 | Must |
| **NFR-004** | Performance — latency target | Native digital PDF extraction (10 pages, typical skill) | `p50 ≤ 20 s`, `p95 ≤ 45 s` | Local benchmark script against fixture corpus | Should (operational goal) |
| **NFR-005** | Performance — latency target | Scanned PDF extraction (10 pages, OCR engaged) | `p50 ≤ 60 s`, `p95 ≤ 120 s` | Local benchmark | Should |
| **NFR-006** | Performance — latency target | PDF annotation overhead (10 pages, 20 fields) over `JSON_ONLY` baseline | `≤ 2 s` additional | Local benchmark | Should |
| **NFR-007** | Performance — cold start | Container boot to `/ready` green with Ollama reachable | `≤ 10 s` | Integration test measures boot time | Should |
| **NFR-008** | Memory | Idle process RSS (warm, no in-flight request) | `≤ 1.5 GB` | Spot check via `ps` / `docker stats`; runbook documented | Should (operational goal) |
| **NFR-009** | Memory | Per-in-flight-request additional RSS (delta over idle) | `≤ 1 GB` | Spot check with representative PDFs | Should |
| **NFR-010** | Memory | Total peak RSS for a single request | `≤ 2.5 GB` | Spot check; exceeding is a regression bug | Should |
| **NFR-011** | Scalability | Concurrency model: one in-flight request at a time; subsequent requests queued serially | `workers=1` Uvicorn config | Integration test fires 2 concurrent requests, asserts both succeed | Must |
| **NFR-012** | Scalability | Horizontal scalability | **Explicitly not required for v1.** Architecture permits it; no testing. | N/A | Won't |
| **NFR-013** | Availability | Uptime SLO | **Explicitly not required for v1.** Service is stateless and restartable. | N/A | Won't |
| **NFR-014** | Availability | Degraded-mode boot when Ollama unreachable | `/ready=503` until Ollama reachable; service self-heals | Integration test | Must |
| **NFR-015** | Availability | Ollama probe TTL | `10 s` (configurable) | Unit test of probe caching layer | Must |
| **NFR-016** | Security | Authentication | **Explicitly not required for v1.** Trusted-network deployment only. | N/A | Won't |
| **NFR-017** | Security | Authorization | **Explicitly not required for v1.** Same rationale as NFR-016. | N/A | Won't |
| **NFR-018** | Security | Data residency — no outbound network calls except to configured Ollama base URL | Zero non-Ollama outbound calls during runtime | Integration test inspects outbound network; import-linter blocks cloud SDK imports | Must |
| **NFR-019** | Security | Log content redaction — no raw PDF content, no extracted field values, no full prompts in logs | No occurrences of forbidden content in captured logs during a mocked extraction | Unit test inspects log records | Must |
| **NFR-020** | Security | No persistent storage of request content | Zero disk writes outside logging during request handling | Unit test monkey-patches file I/O during a mock extraction | Must |
| **NFR-021** | Observability | Structured logging via `structlog` (no `print`, no `logging.getLogger`, no f-string log messages) | Code review + CLAUDE.md forbidden-patterns | Enforced by code review; unit test for log shape | Must |
| **NFR-022** | Observability | Request correlation — every log line emitted during a request carries a stable `request_id` | Integration test asserts presence | Integration test | Must |
| **NFR-023** | Observability | Metrics / distributed tracing / APM | **Explicitly not required for v1.** | N/A | Won't |
| **NFR-024** | Maintainability | TDD discipline — Red-Green-Refactor for every class; no implementation before failing test | CLAUDE.md sacred rule; code review | Code review | Must |
| **NFR-025** | Maintainability | One class per file | No file contains more than one top-level class | Code review; optional custom ruff rule | Must |
| **NFR-026** | Maintainability | Layer enforcement via `import-linter` (feature independence, intra-feature DAG, third-party containment) | `task lint` passes all contracts | `import-linter` run in `task lint` | Must |
| **NFR-027** | Maintainability | Type checking strictness — `pyright --strict` passes with zero errors | Zero errors; `# type: ignore` only with explanatory comment | `task check` runs pyright | Must |
| **NFR-028** | Maintainability | Linting strictness — `ruff check` and `ruff format` pass | Zero errors | `task check` runs ruff | Must |
| **NFR-029** | Maintainability | Test discipline — unit, integration (no DB), contract, optional E2E-slow. No snapshot tests, no assertion-free tests, no `unittest.TestCase` | All test levels green in `task check` | `task check` | Must |
| **NFR-030** | Maintainability | Error system — codes in `packages/error-contracts/errors.yaml`, generated via `task errors:generate`; no hand-edited `_generated/` files | No uncommitted drift between YAML and generated code; CI validates | CLAUDE.md sacred rule | Must |
| **NFR-031** | Usability | Human-facing UI | **Explicitly not required.** API-only service. | N/A | Won't |
| **NFR-032** | Usability | Internationalization | **Explicitly not required.** Error responses are machine-readable codes, not localized strings. | N/A | Won't |
| **NFR-033** | Accessibility | WCAG / screen-reader compliance | **Explicitly not required.** N/A for an API service. | N/A | Won't |
| **NFR-034** | Data | Retention | **Explicitly zero retention.** Nothing persists beyond the response. | N/A | Won't |
| **NFR-035** | Data | Backup / recovery | **Explicitly not required.** Nothing to back up. | N/A | Won't |
| **NFR-036** | Compliance | Formal compliance posture (GDPR / HIPAA / PCI) | **Explicitly deferred to caller's deployment boundary.** The microservice itself makes no compliance claims. Trusted-network assumption defers this upstream. | N/A | Won't |

---

## 7. Constraints, Risks & Assumptions

### Constraints

| ID | Type | Constraint | Impact on Design |
|---|---|---|---|
| **CON-001** | Technical | Python 3.13, `uv` for deps, `pnpm 10` + Node 22 for monorepo tooling | Stack lockdown; no alternatives considered |
| **CON-002** | Technical | FastAPI + Pydantic v2 + pydantic-settings + structlog | Dictates API layer shape and logging idioms |
| **CON-003** | Technical | Docling is the PDF parser | Parsing layer is a thin wrapper; coordinate matching consumes Docling's output shape |
| **CON-004** | Technical | LangExtract is the extraction orchestrator | Forces the `IntelligenceProvider` interface to sit at the model-call level inside LangExtract; chunking/multi-pass/grounding are delegated |
| **CON-005** | Technical | Ollama is the only LLM runtime, external to the container | Boot must tolerate Ollama unreachability (degraded mode); networking assumptions matter (`host.docker.internal`) |
| **CON-006** | Technical | Default model is the smallest Gemma 4 variant, configurable | Dictates retry loop depth and structured output validator design |
| **CON-007** | Technical | PyMuPDF is the PDF annotation library | Annotation layer is isolated behind `PdfAnnotator` |
| **CON-008** | Technical | No database, no persistent storage, no async job queue | Rules out any pattern that assumes state between requests |
| **CON-009** | Technical | Self-hosted only, no cloud dependencies | Rules out any hosted API or managed service |
| **CON-010** | Technical | Synchronous request handling; no background processing | Forces end-to-end timeout to be tight enough to avoid hanging clients |
| **CON-011** | Process | CLAUDE.md sacred rules: one class per file, TDD, no paradigm drift, `task check` must pass before declaring work done | Dictates folder structure, file layout, and review discipline |
| **CON-012** | Process | Error system uses `errors.yaml` + codegen flow | New errors require YAML edit + regeneration + translation validation |
| **CON-013** | Process | Vertical-slice architecture enforced by `import-linter` | Feature independence, intra-feature DAG, third-party containment are mechanical, not advisory |
| **CON-014** | Resource | Development and runtime host is macOS (Apple Silicon implied by current machine) | Container must run on ARM64; amd64 is nice-to-have but not required for v1 |
| **CON-015** | Organizational | Template infrastructure (CI, Dependabot auto-merge, pre-commit, Taskfile) is preserved across the refactor | Scope of stripping is limited to widget/DB/frontend; platform plumbing remains |

### Risks

| ID | Risk | Likelihood | Impact | Mitigation | Owner |
|---|---|---|---|---|---|
| **R-001** | Gemma 4 smallest variant cannot consistently produce valid JSON against a nontrivial skill schema; 4 attempts exhaust without success for frequently-used fields | H | H | `StructuredOutputValidator` retries with correction prompts; per-field partial success so one bad field doesn't kill the whole response; per-skill prompt iteration during authoring; upgrade `Settings.ollama_model` to a larger Gemma 4 variant as fallback | Skill author + spec owner |
| **R-002** | LangExtract community provider plugin API changes break `OllamaGemmaProvider` between versions | M | M | Pin LangExtract version in `pyproject.toml`; isolate plugin code to one file; LangExtract Dependabot PRs are reviewed manually, not auto-merged | Maintainer |
| **R-003** | Character offsets returned by LangExtract drift from Docling block text due to whitespace / ligature / Unicode normalization | H | M | `SubBlockMatcher` three-step fallback (direct → whitespace-normalized → NFKC); whole-block bbox fallback; final `grounded=false` fallback | Coordinate matching code |
| **R-004** | Real-world OCR-heavy scanned PDFs exceed the 180 s timeout, causing systematic `INTELLIGENCE_TIMEOUT` errors on legitimate documents | M | M | Tune Docling OCR per-skill; enforce 200-page limit; raise `Settings.extraction_timeout_seconds` operationally if needed | Operator |
| **R-005** | Ollama crashes mid-request | L | H | Return `INTELLIGENCE_UNAVAILABLE` cleanly; `/ready` probe auto-detects; stateless design means no cleanup required | Operator |
| **R-006** | Malformed or malicious PDF triggers pathological Docling memory usage beyond the 2.5 GB ceiling before the 50 MB byte guard trips | L | H | Rely on byte + page count prefilters; trust Docling's own safeguards; document the ceiling as a regression threshold | Docling (upstream) + operator |
| **R-007** | PyMuPDF annotation produces highlights that render incorrectly in some PDF viewers | L | M | Integration tests with known fixtures; document tested viewer baseline | Maintainer |
| **R-008** | Design spec and requirements spec drift as implementation proceeds | M | M | Treat both as load-bearing; PR review gate includes docs-update check when spec'd components change | Maintainer |
| **R-009** | Pressure to add a second `IntelligenceProvider` implementation before v1 stabilizes | M | L | `FR-W08` explicit refusal; protocol is already designed to accept v2; document "v2" as the answer to such requests | Spec owner |
| **R-010** | The microservice never gets integrated into a real downstream project, leaving v1 under-validated | M | M | E2E test against at least one realistic fixture PDF with a real skill YAML, regardless of downstream project timing | Maintainer |

### Assumptions

| ID | Assumption | What Breaks If Wrong | Cost of Being Wrong | Owner |
|---|---|---|---|---|
| **A-001** | Operator has Ollama installed, running, with a working Gemma 4 tag matching `Settings.ollama_model` | Service starts in permanent degraded mode; no request ever succeeds | H — deployment dead on arrival | Operator (runbook step) |
| **A-002** | Docling default OCR quality is sufficient for realistic scanned documents at common target classes | Partial extractions become common; service appears unreliable | M — degrades trust in output | Benchmark against scanned fixtures |
| **A-003** | LangExtract community provider plugin mechanism is stable enough to register an Ollama-backed provider without Gemini-specific runtime features | Intelligence layer needs redesign; project blocked | H — structural rework | Spike proof-of-concept early |
| **A-004** | Docling's bounding box coordinate system maps directly (or via a known trivial transform) to PyMuPDF's page coordinate system | Highlights render at wrong positions; annotation feature is useless | M — value prop degraded, JSON still works | Integration test with hand-verified fixtures |
| **A-005** | Smallest Gemma 4 variant + correction prompts can produce valid JSON for realistic skill schemas (10–20 fields, mostly string/date) | `STRUCTURED_OUTPUT_FAILED` becomes the common case; default model must upgrade, inflating latency | M — latency budgets tighten | Benchmark during skill authoring |
| **A-006** | Character-offset ratios produce visually acceptable sub-block highlights despite proportional fonts | Highlights visually miss target words even though bbox is "correct by ratio" | L — feature looks broken but is technically correct | Manual visual inspection |
| **A-007** | Trusted-network deployment assumption holds for all realistic v1 consumers | Exposing to untrusted callers becomes a security incident; "no auth" decision retroactively becomes a vulnerability | H — security posture collapses | Runbook documentation; caller responsibility |
| **A-008** | `workers=1` synchronous processing is sufficient throughput for every v1 consumer | Consumer hits throughput limit, must run multiple instances (untested) | L — external operational workaround exists | Caller operational concern |
| **A-009** | Page count (200) is a reasonable proxy for OCR cost | Dense low-page-count PDFs blow through latency budget | L — adjust limit empirically | Observed during benchmarking |
| **A-010** | Dependabot PRs for Docling, LangExtract, PyMuPDF, Ollama client don't silently introduce breaking changes that evade CI | Auto-merge ships a regression | M — fast to detect via test suite | CI test suite + manual-review carve-out for provider-layer deps |

---

## 8. Scope Boundaries

### Explicitly In Scope

- Single endpoint `POST /api/v1/extract` accepting multipart PDF + skill reference + output mode.
- Generic skill system: any well-formed YAML under `skills_dir` registered at startup can be invoked.
- Full extraction pipeline: Docling parsing (native + OCR) → `TextBlock` normalization → `TextConcatenator` + `OffsetIndex` → LangExtract orchestration → `OllamaGemmaProvider` → `StructuredOutputValidator` with 4-attempt correction-prompt loop → `SpanResolver` → conditional `PdfAnnotator` → response serialization for all three output modes.
- Hard limits enforced programmatically: 50 MB max PDF, 200 max pages, 180 s end-to-end timeout.
- Per-field response contract: every declared field always present with `status` / `source` / `grounded` / `bbox_refs`.
- Health + readiness endpoints with Ollama-probe-gated readiness and configurable TTL.
- Startup validation of skills (hybrid manifest); container refuses to start on any malformed YAML.
- Degraded-mode boot when Ollama unreachable; self-healing when Ollama returns.
- Template cleanup: strip widget CRUD, database, Alembic, frontend, api-client package, and CRUD error codes.
- Thin abstractions: `DocumentParser` protocol, `IntelligenceProvider` protocol, provider-agnostic `StructuredOutputValidator`.
- Four-level test discipline: unit / integration (no DB) / contract / optional E2E-slow.
- `import-linter` DAG enforcing third-party dependency containment (Docling, PyMuPDF, LangExtract, Ollama client).
- Structured logging via `structlog` with request correlation and forbidden-content redaction.

### Explicitly Out of Scope

- **Authentication / authorization / rate limiting** — trusted-network deployment only.
- **Persistent storage** — no database, no disk writes, no request history, no caching.
- **Async / streaming / background jobs** — all requests are synchronous and atomic.
- **Frontend / CLI / human-facing UI** — API + OpenAPI only.
- **Multi-tenant isolation** — single-user deployment model.
- **Second `IntelligenceProvider` implementation** — protocol is ready for a future Claude / GPT-4 / larger-Gemma provider but only `OllamaGemmaProvider` ships in v1.
- **Skill hot-reload** — manifest is frozen at startup.
- **Horizontal scalability testing** — architecturally permitted, not validated.
- **Observability beyond structlog** — no Prometheus, no OpenTelemetry, no Sentry, no APM.
- **Internationalization / accessibility** — N/A for API service.
- **Compliance posture (GDPR / HIPAA / PCI)** — deferred to caller's deployment boundary.
- **Per-skill `allow_inference: bool`** — ungrounded values always returned with a flag; caller decides.
- **Model fine-tuning** — operator responsibility if ever needed.
- **Non-PDF input formats** — PDFs only.

### Open Questions

| ID | Question | Owner | Deadline | Default if Unresolved |
|---|---|---|---|---|
| **OQ-001** | Design-spec correction: Section 5.2 says `SpanResolver` is skipped for `PDF_ONLY`, but `PdfAnnotator` requires `bbox_refs`, so coordinate matching must run regardless. FR-039 defines the correct behavior. | Spec owner | Before implementation begins | This requirements spec is authoritative; amend the design spec to match FR-039 |
| **OQ-002** | Exact Ollama model tag for the smallest Gemma 4 variant at implementation time (the tag list on the Ollama registry evolves) | Implementer | Set in `.env.example` before first successful extraction test | Pin a conservative smallest-Gemma tag; document in runbook |
| **OQ-003** | Default `Settings.ollama_base_url` — `host.docker.internal:11434` (Docker-for-Mac) vs `http://localhost:11434` (non-containerized dev) | Implementer | Before writing `infra/compose/docker-compose.yml` | Ship `host.docker.internal` and document the non-containerized alternative |
| **OQ-004** | Exact `CorrectionPromptBuilder` template wording for retry attempts; needs empirical tuning against Gemma 4 failure modes | Implementer | During integration test authoring | Start with a minimal template (reiterate schema, show malformed output, ask for correction) and iterate |
| **OQ-005** | Whether `app/shared/base_service.py` survives the template cleanup; depends on whether its interface is database-coupled | Implementer | During template cleanup step | Remove it; `ExtractionService` is a plain class |

---

## 9. Agile Hierarchy Skeleton

> Consumed by downstream planning. Each Feature maps to a subpackage in the design spec's Section 4 folder structure. Stories are listed for Must-have features at a granularity sufficient for implementation breakdown; tasks are generated during the writing-plans phase, not here.

### Theme: PDF Data Extraction Microservice

**Epic E-01 — Template Cleanup & Refactor Preparation**
Remove widget CRUD, database layer, frontend, and CRUD error contracts to clear the way for the extraction feature.

- **Feature F-01-01 — Backend template strip**
  - **Story S-01-01-01** — As the integrating developer, I want the widget feature, database layer, and alembic directory removed, so that the codebase reflects the no-database architecture.
    - Acceptance: `apps/backend/app/features/widget/`, `app/core/database.py`, `alembic/` no longer exist; `task check` still passes.
    - Linked: CON-015
  - **Story S-01-01-02** — As the integrating developer, I want DB-related dependencies stripped from `pyproject.toml`, so that `uv sync` pulls only what the extraction feature needs.
    - Acceptance: `sqlalchemy`, `alembic`, `asyncpg` removed; `docling`, `langextract`, `pymupdf`, `jsonschema`, `httpx`, `pyyaml` added.
- **Feature F-01-02 — Frontend removal**
  - **Story S-01-02-01** — As the integrating developer, I want `apps/frontend/` and `packages/api-client/` removed and related compose/docker config stripped.
    - Acceptance: frontend directories gone; docker-compose has no frontend service; CI still passes.
- **Feature F-01-03 — Error contract migration**
  - **Story S-01-03-01** — As the integrating developer, I want CRUD-specific error codes removed from `errors.yaml` and the new extraction error codes added.
    - Acceptance: `WIDGET_*` codes removed; `SKILL_NOT_FOUND`, `SKILL_VALIDATION_FAILED`, `PDF_INVALID`, `PDF_TOO_LARGE`, `PDF_TOO_MANY_PAGES`, `PDF_PASSWORD_PROTECTED`, `PDF_NO_TEXT_EXTRACTABLE`, `INTELLIGENCE_UNAVAILABLE`, `INTELLIGENCE_TIMEOUT`, `STRUCTURED_OUTPUT_FAILED` added; `task errors:generate` clean; translations updated.
    - Linked: FR-002, FR-003, FR-008, FR-010, FR-012, FR-016 through FR-020, FR-026, FR-027

**Epic E-02 — Parsing Layer (`parsing/`)**
The Docling abstraction — a `DocumentParser` protocol plus a `DoclingDocumentParser` implementation that emits plain `TextBlock` data structures.

- **Feature F-02-01 — `DocumentParser` protocol and types**
  - **Story S-02-01-01** — As the implementer, I want `DocumentParser`, `ParsedDocument`, `TextBlock`, `BoundingBox` defined in isolation, so that the rest of the pipeline can import only these types.
    - Acceptance: all four classes exist in separate files under `parsing/`; no Docling imports in any of them; unit tests pass with fake instances.
    - Linked: FR-014, FR-020
- **Feature F-02-02 — `DoclingDocumentParser` implementation**
  - **Story S-02-02-01** — As the implementer, I want a Docling-backed parser that produces `ParsedDocument`s for native PDFs.
    - Linked: FR-014
  - **Story S-02-02-02** — As the implementer, I want the same parser to trigger OCR for scanned PDFs.
    - Linked: FR-015
  - **Story S-02-02-03** — As the implementer, I want the parser to enforce the page-count limit early, raise `PDF_PASSWORD_PROTECTED` for encrypted PDFs, and raise `PDF_NO_TEXT_EXTRACTABLE` when nothing comes back.
    - Linked: FR-003, FR-017, FR-018
  - **Story S-02-02-04** — As the implementer, I want the parser to accept a merged Docling config (global defaults overlaid by per-skill overrides).
    - Linked: FR-019

**Epic E-03 — Intelligence Layer (`intelligence/`)**
The LLM provider abstraction and the Gemma-4-via-Ollama implementation plus the provider-agnostic structured output validator.

- **Feature F-03-01 — Protocol and supporting types**
  - **Story S-03-01-01** — As the implementer, I want the `IntelligenceProvider` protocol and `GenerationResult` defined.
    - Linked: FR-021
- **Feature F-03-02 — `StructuredOutputValidator`**
  - **Story S-03-02-01** — As the implementer, I want cleanup (strip fences, extract JSON, parse) tested against a library of malformed inputs.
    - Linked: FR-024
  - **Story S-03-02-02** — As the implementer, I want the retry loop with `CorrectionPromptBuilder` to invoke a regeneration callable up to 3 times on failure.
    - Linked: FR-025
- **Feature F-03-03 — `OllamaGemmaProvider`**
  - **Story S-03-03-01** — As the implementer, I want a provider class that satisfies both `IntelligenceProvider` and LangExtract's plugin contract, with the Ollama HTTP client contained inside the file.
    - Linked: FR-022, FR-028
  - **Story S-03-03-02** — As the implementer, I want the default model tag sourced from `Settings.ollama_model` with the smallest-Gemma-4 default.
    - Linked: FR-023
  - **Story S-03-03-03** — As the implementer, I want connection failures and timeouts to map cleanly to `INTELLIGENCE_UNAVAILABLE`.
    - Linked: FR-027

**Epic E-04 — Extraction Orchestration (`extraction/`)**
LangExtract integration.

- **Feature F-04-01 — `ExtractionEngine`**
  - **Story S-04-01-01** — As the implementer, I want `ExtractionEngine` to call LangExtract with the skill's prompt/examples/schema and route provider calls to the injected `IntelligenceProvider`.
    - Linked: FR-021, FR-029

**Epic E-05 — Skill System (`skills/`)**
YAML loader, manifest, startup validation.

- **Feature F-05-01 — Skill types and schema**
  - **Story S-05-01-01** — As the implementer, I want `Skill`, `SkillYamlSchema`, `SkillLoader`, and `SkillManifest` defined in separate files.
- **Feature F-05-02 — Startup validation and `latest` resolution**
  - **Story S-05-02-01** — As the implementer, I want `SkillManifest` to validate every YAML at startup, including verifying examples match the declared schema.
    - Linked: FR-009
  - **Story S-05-02-02** — As the implementer, I want the container to refuse to start when any skill fails validation.
    - Linked: FR-010
  - **Story S-05-02-03** — As the implementer, I want `latest` resolved to the highest registered integer version per skill name.
    - Linked: FR-011, FR-013
  - **Story S-05-02-04** — As the implementer, I want unknown `(name, version)` pairs to raise `SKILL_NOT_FOUND` (404).
    - Linked: FR-012

**Epic E-06 — Coordinate Matching (`coordinates/`)**
Offset index, sub-block matcher, span resolver.

- **Feature F-06-01 — `TextConcatenator` and `OffsetIndex`**
  - **Story S-06-01-01** — As the implementer, I want concatenation and offset indexing to produce a deterministic, binary-searchable lookup structure.
    - Linked: FR-030
- **Feature F-06-02 — `SubBlockMatcher`**
  - **Story S-06-02-01** — As the implementer, I want direct, whitespace-normalized, and NFKC-normalized substring matching in a fallback chain.
    - Linked: FR-031
- **Feature F-06-03 — `SpanResolver`**
  - **Story S-06-03-01** — As the implementer, I want single-block spans resolved via sub-block matching with whole-block fallback.
    - Linked: FR-031, FR-032
  - **Story S-06-03-02** — As the implementer, I want multi-block and cross-page spans to produce multiple `BoundingBoxRef` entries.
    - Linked: FR-032
  - **Story S-06-03-03** — As the implementer, I want unmatched spans to produce `grounded=false` fields with empty `bbox_refs`.
    - Linked: FR-033, FR-037

**Epic E-07 — Annotation (`annotation/`)**
PyMuPDF highlight rendering.

- **Feature F-07-01 — `PdfAnnotator`**
  - **Story S-07-01-01** — As the implementer, I want `PdfAnnotator` to draw highlights at each `BoundingBoxRef` and return annotated PDF bytes, containing all PyMuPDF imports to this file.
    - Linked: FR-038, FR-040

**Epic E-08 — API Layer (`router.py` + `schemas/` + `service.py`)**
The thin FastAPI shell and the orchestrating service.

- **Feature F-08-01 — Request / response schemas**
  - **Story S-08-01-01** — As the implementer, I want `ExtractRequest`, `ExtractResponse`, `ExtractedField`, `BoundingBoxRef`, `OutputMode`, `FieldStatus` defined one per file as Pydantic models.
    - Linked: FR-001, FR-034, FR-035
- **Feature F-08-02 — `ExtractionService`**
  - **Story S-08-02-01** — As the implementer, I want `ExtractionService.extract` to orchestrate the full pipeline with linear conditional guards for output-mode differences.
    - Linked: FR-039
  - **Story S-08-02-02** — As the implementer, I want the service to enforce the end-to-end 180 s timeout via `asyncio.timeout`.
    - Linked: FR-007
- **Feature F-08-03 — Router + response serialization**
  - **Story S-08-03-01** — As the implementer, I want the router to accept multipart uploads, size-check them, call the service, and serialize per output mode.
    - Linked: FR-001, FR-002, FR-004, FR-005, FR-006, FR-008
  - **Story S-08-03-02** — As the implementer, I want a small multipart-mixed response helper for `BOTH` mode.
    - Linked: FR-006

**Epic E-09 — Platform: Health, Readiness, Logging**
Operational endpoints and cross-cutting observability.

- **Feature F-09-01 — Health and readiness**
  - **Story S-09-01-01** — As the operator, I want `/health` to always return 200 if the process is alive.
    - Linked: FR-041
  - **Story S-09-01-02** — As the operator, I want `/ready` to gate on an Ollama probe with a 10-second TTL so orchestrators hold traffic until the service can actually serve requests.
    - Linked: FR-042
  - **Story S-09-01-03** — As the operator, I want the service to start in degraded mode when Ollama is unreachable at boot and to self-heal when Ollama returns.
    - Linked: FR-043, NFR-014
- **Feature F-09-02 — Structured logging**
  - **Story S-09-02-01** — As the operator, I want every request to produce a structured log entry with request id, skill, duration, outcome, per-field attempt counts, but no raw content.
    - Linked: FR-045, NFR-019, NFR-021, NFR-022

**Epic E-10 — Benchmarks and Quality Gates**
Measurement and enforcement of NFRs.

- **Feature F-10-01 — Latency benchmarks**
  - **Story S-10-01-01** — As the maintainer, I want a local benchmark script to measure latency against native and scanned fixture PDFs so I can check the NFR targets after a run.
    - Linked: NFR-004, NFR-005, NFR-006, NFR-007
- **Feature F-10-02 — `import-linter` contracts**
  - **Story S-10-02-01** — As the maintainer, I want `import-linter` contracts for feature independence, intra-feature DAG, and third-party containment enforced in `task lint`.
    - Linked: FR-020, FR-028, FR-029, FR-040, NFR-026

---

## 10. Testing Strategy

| Requirement Type | Test Approach | Tools / Method |
|---|---|---|
| Must FRs (behavioral) | Unit + Integration | `pytest` with `httpx.AsyncClient` against FastAPI app; stub `IntelligenceProvider` via `Depends` override for determinism |
| Must FRs (architectural — FR-020, FR-028, FR-029, FR-040) | Static analysis | `import-linter` contracts, run in `task lint` |
| Contract (OpenAPI surface) | Contract testing | `schemathesis` against `POST /api/v1/extract` |
| NFR Performance (latency targets NFR-004, NFR-005, NFR-006, NFR-007) | Local benchmark | Custom benchmark script against fixture PDF corpus; not asserted in CI; baseline tracked in runbook |
| NFR Performance (hard limits NFR-001, NFR-002, NFR-003) | Integration | `pytest` asserts 413/504 at boundary inputs |
| NFR Memory (NFR-008, NFR-009, NFR-010) | Manual | `ps` / `docker stats` spot check documented in runbook; regressions tracked visually |
| NFR Security (NFR-018, NFR-019, NFR-020) | Unit + Integration | Monkey-patched file I/O and network calls; log record inspection |
| NFR Maintainability (NFR-024 through NFR-030) | CI gates | `task check` runs pyright strict, ruff, tests, import-linter, error-contract validation |
| Optional E2E (slow) | Realistic round-trip | One E2E test with a real Ollama + real Gemma 4 + a fixture skill + a fixture PDF, marked `slow`, excluded from default `task check`, runnable via `task test:slow` |

### Integration Testing Notes

- **Database-free integration.** Because there is no database, "integration" means the full router → service → pipeline chain with everything real except the `IntelligenceProvider`, which is stubbed via `Depends` override for deterministic responses.
- **Real Docling against fixture PDFs.** A small corpus of test PDFs (`tests/fixtures/`): one native digital multi-page, one scanned multi-page, one encrypted, one blank, one intentionally corrupted. These exercise FR-014 through FR-018 without touching Ollama.
- **Real PyMuPDF round-trip.** Annotation is tested end-to-end against fixture PDFs: annotate, reopen the result with PyMuPDF, assert the annotations land on expected pages.
- **Multipart response parsing.** For `BOTH` mode, a test uses a multipart parser (e.g. `requests-toolbelt`) to deserialize the response and assert both parts are well-formed.
- **Ollama probe isolation.** `/ready` and degraded-mode tests use a mock Ollama HTTP endpoint (or a raw TCP socket that refuses connections) to exercise the probe logic without requiring a real Ollama instance.

### Code Quality Pipeline

- **Pre-commit:** ruff format, trailing whitespace, end-of-file, YAML / JSON validity, large-file guard. Pre-commit failure blocks local commits; never bypassed with `--no-verify`.
- **`task check` (local and CI):**
  - `ruff check`
  - `ruff format --check`
  - `pyright --strict` with zero errors
  - `import-linter` (feature independence, intra-feature DAG, third-party containment)
  - `pytest` unit + integration + contract
  - `task errors:check` (error-contract / translation validation)
- **CI additionally:** full `task check` on every PR; Dependabot auto-merge only for green PRs once the branch protection ruleset covers all required checks.
- **Test coverage:** no enforced numeric target for v1. TDD discipline (every class has a failing test first) is the coverage mechanism; explicit coverage percentage gates add ceremony without value at this stage.
- **Review process:** every PR that modifies a `Must` FR updates this requirements spec and/or the design spec in the same PR; mismatches are a PR-review block.
