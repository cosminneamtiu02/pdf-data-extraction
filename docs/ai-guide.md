# AI Guide — Post-Bootstrap Shell Overview

What is already implemented in the post-bootstrap shell, what is not, and how
the pieces connect. Read [`CLAUDE.md`](../CLAUDE.md) for the rules and forbidden
patterns.

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
- [`docs/graphs/PDFX/`](graphs/PDFX/) — 1 project + 7 epics + 29 features, all at `status: detailed`

## Backend — What's Built (post-bootstrap shell)

**Core infrastructure.** App factory (`app/main.py`), configuration via
pydantic-settings (`app/core/config.py`), structured logging via structlog
(`app/core/logging.py`), FastAPI dependency injection for settings
(`app/api/deps.py`).

**Middleware stack.** Request-ID generation and validation (UUID-only, prevents
log injection), access logging with method/path/status/duration, CORS with
configurable origins. Security headers middleware was removed during the
bootstrap run since the service runs on a trusted network only. All wired in
`app/api/middleware.py`.

**Error handling.** Fully implemented via a code-generated system. Error codes
live in `packages/error-contracts/errors.yaml`; a codegen script produces one
Python exception class per error code in `app/exceptions/_generated/`. A single
exception handler in `app/api/errors.py` maps all `DomainError` subclasses to a
consistent JSON response shape: `{error: {code, params, details, request_id}}`.
The post-bootstrap shell ships four generic error codes (`NOT_FOUND`,
`CONFLICT`, `VALIDATION_FAILED`, `INTERNAL_ERROR`). Extraction-specific codes
(skill lookup, PDF parsing, intelligence layer, API layer) are added as
feature-dev lands the corresponding features.

**Health endpoints.** `/health` is a pure liveness probe returning
`{"status":"ok"}`. `/ready` is a minimal stub returning `{"status":"ready"}`;
during feature-dev PDFX-E007-F001 replaces it with an Ollama-probe-gated
version.

**Architecture enforcement.** `import-linter` is wired in `task lint` with one
contract: `shared/` and `core/` cannot import from `features/`. The full
extraction-feature layer DAG and third-party containment contracts are added by
PDFX-E007-F004 during feature-dev.

## What Is NOT Built — To Be Added by feature-dev

### No extraction feature (yet)

The `features/extraction/` subpackage does not exist in the post-bootstrap
shell. Feature-dev builds it out according to the 29 features in
[`docs/graphs/PDFX/`](graphs/PDFX/). The expected shape after feature-dev:

```
apps/backend/app/features/extraction/
├── router.py
├── service.py
├── schemas/
├── parsing/
├── intelligence/
├── extraction/
├── skills/
├── coordinates/
└── annotation/
```

### No skill YAMLs

The `apps/backend/skills/` data directory does not exist. Feature-dev creates
it during PDFX-E002 and skill authors populate it.

### No authentication or authorization

The API is fully open and runs on a trusted network only. Any auth is the
caller's deployment responsibility, not the service's.

### No persistent storage, no database

The service is stateless. No SQLAlchemy, no Alembic, no Postgres. Every
request is self-contained and produces zero disk writes outside logging.

### No frontend, no API client

The service is API-only. Its consumer is code, not a browser.

## How Things Bind Together (current shell)

**Config → Middleware → Router.** `Settings` (from env via pydantic-settings) is
constructed once at startup in `app/main.py`, drives middleware configuration
(CORS origins), and is available to any downstream handler via
`Depends(get_settings)`.

**Error handling.** Any layer that raises a `DomainError` subclass gets
serialized via the registered exception handler into a consistent response
envelope. The error code is machine-readable and stable.

**Request correlation.** `RequestIdMiddleware` generates a UUID per request and
binds it to `structlog.contextvars` so every log line inside the request scope
carries a `request_id` field. The ID is also set on the response header
`X-Request-ID`.

**Error contracts.** `errors.yaml` is the source of truth. `task errors:generate`
produces Python classes (plus TypeScript and JSON outputs with no current
consumer). Adding a new error is one YAML edit plus `task errors:generate`.

## Next Steps (feature-dev)

The graph tree in [`docs/graphs/PDFX/`](graphs/PDFX/) lists 29 thickened
features in topological priority order (10 through 290). Each feature file
contains Problem, Scope, Out of scope, Acceptance criteria, Dependencies,
Technical constraints, and Open questions. Start at priority 60 (PDFX-E002-F001
is the first feature after template cleanup is already applied by this
bootstrap run) and walk the tree in priority order.
