---
type: epic
id: PDFX-E006
parent: PDFX
title: API Surface & PDF Annotation
status: fully-detailed
priority: 60
dependencies: [PDFX-E002, PDFX-E003, PDFX-E004, PDFX-E005]
---

## Short description

Expose the full extraction pipeline as a single HTTP endpoint (`POST /api/v1/extract`) with multipart request handling, three output modes (`JSON_ONLY` / `PDF_ONLY` / `BOTH`), a multipart/mixed response for the `BOTH` case, an `ExtractionService` that orchestrates the pipeline under a 180-second end-to-end timeout, and a PyMuPDF-based `PdfAnnotator` that draws highlights at each extracted field's bounding boxes.

## Description

This Epic is the entire public contract of the microservice. It owns the router file, the request and response Pydantic schemas, the `ExtractionService` orchestrator, and the `PdfAnnotator` annotation component. The router accepts `multipart/form-data` with four fields: `pdf` (`UploadFile`), `skill_name` (string), `skill_version` (string — positive integer or `latest`), and `output_mode` (enum). It performs the byte-size prefilter (rejecting with `PDF_TOO_LARGE` 413 before any parsing is allocated), hands off to `ExtractionService.extract`, and serializes the result per output mode: `JSON_ONLY` emits `application/json` containing an `ExtractResponse`; `PDF_ONLY` emits `application/pdf` containing the annotated PDF bytes with no JSON envelope; `BOTH` emits `multipart/mixed` with exactly two parts in order — a JSON part and a PDF part — built via a small hand-rolled multipart response helper. `ExtractionService.extract` is the orchestrator: it resolves the skill, parses the PDF, concatenates text, invokes the extraction engine, runs the span resolver, and conditionally invokes the annotator for modes that need PDF output. The entire pipeline runs under an `asyncio.timeout(Settings.extraction_timeout_seconds)` budget; exceeding it raises `INTELLIGENCE_TIMEOUT` (504). The annotator lives here rather than in its own subpackage because annotation is only ever triggered by the router's output-mode branching; keeping it bundled with the API surface keeps "what comes out of the endpoint" as one deliverable. The router also handles FastAPI native validation errors for missing form fields and invalid enum values (422).

## Rationale

This Epic exists because everything upstream is plumbing — the service has no value until it can be called over HTTP and return the right thing in the right shape. The service orchestration layer owns the timeout budget (because it's the only layer that sees the whole pipeline), the output-mode branching (because only here is it clear which of the three artifacts to produce and return), and the response serialization (because that's an HTTP-level concern, not a business-logic concern). Folding `PdfAnnotator` into this Epic reflects the observation that annotation is a terminal step in two of the three output modes and is never invoked except by the router's branching — splitting it into its own Epic would create a seam without a reason. It traces to project success criteria **"all latency criteria"** (the 180 s budget lives here), **"API stability — every declared field always present"** (the response shape invariant is enforced in `ExtractionService` and the schema), and **"hard limits — 50 MB, 180 s"** (the byte-size prefilter and the timeout enforcement).

## Boundary

**In scope:** `features/extraction/router.py`, `features/extraction/service.py`, `features/extraction/schemas/` (all Pydantic request and response models — `ExtractRequest`, `ExtractResponse`, `ExtractedField`, `BoundingBoxRef`, `ExtractionMetadata`, `OutputMode`, `FieldStatus`), `features/extraction/annotation/pdf_annotator.py`; the byte-size prefilter (`PDF_TOO_LARGE`); the end-to-end timeout enforcement (`INTELLIGENCE_TIMEOUT`); the multipart/mixed response builder; the response invariant that every declared field is present; the output-mode branching (`JSON_ONLY` / `PDF_ONLY` / `BOTH`); `import-linter` containment of PyMuPDF imports to `pdf_annotator.py` (and, if needed, `DoclingDocumentParser` for password-detection preflight); the new error code `PDF_TOO_LARGE` added to `errors.yaml`; integration tests for every output mode and every failure mode surfaced by downstream Epics.

**Out of scope:** `/health` and `/ready` endpoints (those live in PDFX-E007, which is the platform envelope around this Epic); middleware for structured logging and request ID correlation (PDFX-E007); any changes to `app/main.py` wiring beyond adding the extraction router; benchmarks against fixture PDFs (PDFX-E007); the `import-linter` contracts themselves (defined in PDFX-E007, even though this Epic respects them).

## Open questions

*This list is not exhaustive. Additional questions may surface during feature elicitation.*

- Whether the multipart/mixed response helper is a standalone utility in `app/shared/` or inlined in the router (it's small). Default: inlined in the router — ~15 lines, not worth a shared utility.
- Whether the `ExtractionResponse.metadata` field includes `parser_warnings` from Docling (e.g. "OCR engaged but confidence was low"), or whether those go only to structlog. Default: optional warnings list in `metadata`; if Docling doesn't surface warnings in a clean form, log only and omit the field.
- Exact `Content-Disposition` headers for the multipart parts. Default: `Content-Disposition: form-data; name="result"` for the JSON part and `name="pdf"; filename="annotated.pdf"` for the PDF part.
- Whether `PdfAnnotator` draws on a copy of the original PDF bytes or mutates a PyMuPDF document opened from those bytes. Default: open the bytes with PyMuPDF, draw annotations, save to a new bytes buffer — no mutation of the input bytes.
- Whether the byte-size prefilter happens via FastAPI's `UploadFile.size` property (which may not be immediately available) or via a streaming read guard. Default: streaming read with an early abort — the byte count is tracked during the read, and as soon as it exceeds `max_pdf_bytes` the request is aborted.
