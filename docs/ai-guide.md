# AI Guide — Implemented Service Overview

What is already implemented, how the pieces fit together, and what is
deliberately out of scope. Read [`CLAUDE.md`](../CLAUDE.md) for the rules and
forbidden patterns.

## What the Project Is

A self-hosted PDF data extraction microservice. Stateless, synchronous,
single-worker FastAPI service that parses PDFs with Docling, orchestrates
structured-data extraction via LangExtract against a locally running Gemma 4
model through a custom Ollama provider, maps values back to PDF coordinates,
optionally draws PyMuPDF highlights, and returns JSON, an annotated PDF, or
both. The only external runtime dependency is Ollama running on the host.

Full context lives in:

- [`docs/superpowers/specs/2026-04-13-pdf-extraction-microservice-design.md`](superpowers/specs/2026-04-13-pdf-extraction-microservice-design.md)
- [`docs/superpowers/specs/2026-04-13-pdf-extraction-microservice-requirements.md`](superpowers/specs/2026-04-13-pdf-extraction-microservice-requirements.md)
- [`docs/graphs/PDFX/`](graphs/PDFX/) — 1 project + 7 epics + 29 features. Each
  feature file's frontmatter carries its current `status`; consult the graph
  tree for the authoritative per-feature state rather than assuming any
  particular feature here is built or unbuilt.

## Backend — What's Built

**Core infrastructure.** App factory (`app/main.py`), configuration via
pydantic-settings (`app/core/config.py`), structured logging via structlog
(`app/core/logging.py`), FastAPI dependency injection for settings
(`app/api/deps.py`).

**Middleware stack.** Request-ID generation and validation (UUID-only, prevents
log injection), access logging with method/path/status/duration, CORS with
configurable origins, upload-size limit middleware. Security headers
middleware was removed during the bootstrap run since the service runs on a
trusted network only. All wired in `app/api/middleware.py`.

**Error handling.** Fully implemented via a code-generated system. Error codes
live in `packages/error-contracts/errors.yaml`; a codegen script produces one
Python exception class per error code in `app/exceptions/_generated/`. A single
exception handler in `app/api/errors.py` maps all `DomainError` subclasses to a
consistent JSON response shape: `{error: {code, params, details, request_id}}`.
Alongside the three generic codes (`NOT_FOUND`, `VALIDATION_FAILED`,
`INTERNAL_ERROR`), the HTTP-returned extraction/runtime codes are:
`SKILL_NOT_FOUND`, `PDF_INVALID`, `PDF_PASSWORD_PROTECTED`, `PDF_TOO_LARGE`,
`PDF_TOO_MANY_PAGES`, `PDF_NO_TEXT_EXTRACTABLE`, `PDF_PARSER_UNAVAILABLE`,
`INTELLIGENCE_UNAVAILABLE`, `INTELLIGENCE_TIMEOUT`,
`STRUCTURED_OUTPUT_FAILED`, `EXTRACTION_BUDGET_EXCEEDED`,
`EXTRACTION_OVERLOADED`. The contract also defines `SKILL_VALIDATION_FAILED`,
but that is a startup/operator failure raised during `create_app()` when a
skill YAML fails to validate; clients should not expect it as an HTTP API
response.

**Health endpoints.** `/health` is a pure liveness probe returning
`{"status":"ok"}`. `/ready` is gated on a TTL-cached Ollama probe
(PDFX-E007-F001): returns 200 when Ollama is reachable within the
configured TTL, or 503 with `{"status":"not_ready","reason":"ollama_unreachable"}`
otherwise.

**Architecture enforcement.** `import-linter` is wired as `task check:arch`, a
direct dependency of `task check` (issue #215; `task lint` is ruff-only). The
bootstrap shell originally shipped with one contract — `shared/` and `core/`
cannot import from `features/`. PDFX-E007-F004 layered in the full
extraction-feature layer DAG and third-party containment contracts; the
contract set (C1–C6) now lives in
[`apps/backend/architecture/import-linter-contracts.ini`](../apps/backend/architecture/import-linter-contracts.ini),
and a companion AST-scan unit test (`test_dynamic_import_containment.py`)
catches dynamic / cross-feature imports that import-linter's static graph
cannot see.

**Extraction vertical slice.** The `features/extraction/` subpackage exists and
is populated. The `POST /api/v1/extract` route is wired. The service composes
the full pipeline: skill lookup -> PDF parsing (Docling) -> text concatenation
-> LLM-driven extraction (LangExtract + Ollama-Gemma provider with
structured-output validation) -> coordinate resolution -> optional PyMuPDF
annotation. Current layout under `apps/backend/app/features/extraction/`:

```
router.py                       # POST /api/v1/extract
service.py                      # ExtractionService pipeline orchestrator
deps.py                         # per-component DI factories for test overrides
extraction_result.py            # pipeline-result value object
schemas/                        # request/response Pydantic models, OutputMode,
                                # ExtractedField, ExtractionMetadata, etc.
parsing/                        # DoclingDocumentParser, DoclingConfig,
                                # BoundingBox, ParsedDocument, PDF preflight
intelligence/                   # IntelligenceProvider Protocol,
                                # OllamaGemmaProvider, OllamaHealthProbe,
                                # StructuredOutputValidator,
                                # CorrectionPromptBuilder,
                                # LangExtract wrapper schema
extraction/                     # ExtractionEngine (LangExtract orchestration)
                                # and validating LangExtract adapter
skills/                         # Skill domain object, SkillManifest,
                                # SkillLoader (YAML -> Skill),
                                # duplicate-key-safe YAML loader
coordinates/                    # SpanResolver, SubBlockMatcher,
                                # TextConcatenator, OffsetIndex, CharRange
annotation/                     # PdfAnnotator (PyMuPDF highlights)
```

Skill YAMLs live under `apps/backend/skills/` (the canonical data directory
resolved by `Settings.skills_dir`). The directory is checked into the repo as
an empty location (`.gitkeep`); it is populated per deployment with the skill
files the service should advertise.

## What Is Deliberately Out of Scope

### No authentication or authorization

The API is fully open and runs on a trusted network only. Any auth is the
caller's deployment responsibility, not the service's.

### No persistent storage, no database

The service is stateless. No SQLAlchemy, no Alembic, no Postgres. Every
request is self-contained and produces zero disk writes outside logging.

### No frontend, no API client

The service is API-only. Its consumer is code, not a browser.

## How Things Bind Together

**Config -> Middleware -> Router.** `Settings` (from env via pydantic-settings)
is constructed once at startup in `app/main.py`, drives middleware
configuration (CORS origins, upload byte cap), and is available to any
downstream handler via `Depends(get_settings)`.

**Extraction request lifecycle.** Byte-size enforcement on `POST /api/v1/extract`
is a defense-in-depth pair, not a single gate. `UploadSizeLimitMiddleware` is
the true streaming-time guard: it runs at the ASGI layer before route dispatch,
inspects `Content-Length`, and rejects oversized or missing/ambiguous-length
requests (fail-closed) before Starlette's multipart parser spools any bytes.
By the time `router.extract` receives a FastAPI `UploadFile`, Starlette has
already parsed and spooled the multipart upload, so the route-level
`read_with_byte_limit` check is a secondary safeguard if the ASGI guard is
bypassed, misconfigured, or not applied to a given upload route (for example,
a newly added upload endpoint not covered by the guarded paths). It is not the
mechanism that prevents multipart spooling on a correctly guarded route.
`router.extract` then calls `ExtractionService.extract(...)`, which runs the
pipeline under a single `asyncio.timeout` budget
(`Settings.extraction_timeout_seconds`) and a per-service semaphore cap
(`Settings.max_concurrent_extractions`) so over-cap callers fail fast with
`ExtractionOverloadedError` (503) instead of queueing. Timed-out background
work (Docling / Ollama) is allowed to finish but its result is discarded.

**Error handling.** Any layer that raises a `DomainError` subclass gets
serialized via the registered exception handler into a consistent response
envelope. The error code is machine-readable and stable. Expected,
user-facing failures inside the extraction pipeline do NOT surface as bare
`ValueError` / `RuntimeError`; they raise a generated `DomainError` subclass
from `errors.yaml`. Value-object constructors (e.g. `CharRange`,
`BoundingBox`, `DoclingConfig`) still raise `ValueError` from their
`__post_init__` / `__init__` invariant checks because those guard against
programmer errors — wrong arguments at call-site — not runtime pipeline
failures; see `CLAUDE.md` for the carve-out.

**Request correlation.** `RequestIdMiddleware` generates a UUID per request
and binds it to `structlog.contextvars` so every log line inside the request
scope carries a `request_id` field. The ID is also set on the response header
`X-Request-ID`.

**Error contracts.** `errors.yaml` is the source of truth. `task errors:generate`
produces Python classes (plus TypeScript and JSON outputs). Adding a new error
is one YAML edit plus `task errors:generate`.

## Guidance for New AI Agents

- Treat `docs/graphs/PDFX/` as the authoritative per-feature status; do NOT
  rely on prose in this guide to determine whether a specific feature is
  implemented. A feature file's frontmatter `status` field is load-bearing.
- Before editing code that touches a third-party library (Docling, LangExtract,
  PyMuPDF, Ollama HTTP client), use Context7 to fetch current docs. Training
  data may lag.
- Every new extraction path must raise a `DomainError` subclass from
  `errors.yaml`, never a bare `ValueError` / `RuntimeError` / `HTTPException`.
- Third-party imports are containment-locked to specific files (see
  `CLAUDE.md` and `import-linter-contracts.ini`); add new Docling / PyMuPDF /
  LangExtract / Ollama-HTTP imports only in the designated containment files.
