# Features Catalog

Every capability already implemented in this template after the PDFX bootstrap
run, each with a short description. The extraction feature itself is NOT listed
here — see [`docs/graphs/PDFX/`](graphs/PDFX/) for the full epic + feature tree
that feature-dev implements.

## Backend — Core Infrastructure

### Typed Settings ([app/core/config.py](../apps/backend/app/core/config.py))
Pydantic-settings based configuration with defaults for `APP_ENV`, `LOG_LEVEL`,
and `CORS_ORIGINS`. Extraction-specific settings (Ollama base URL, model tag,
limits, timeouts) are added during feature-dev as the corresponding features
land.

### Structured Logging ([app/core/logging.py](../apps/backend/app/core/logging.py))
Structlog pipeline with contextvar merging, ISO timestamps, and JSON output in
production / console output in dev. Noisy loggers (`uvicorn.access`) are silenced
at WARNING so real signal isn't buried.

### Request ID Middleware ([app/api/middleware.py](../apps/backend/app/api/middleware.py))
Validates incoming `X-Request-ID` headers against a UUID regex, generates a fresh
UUID4 if missing or malformed, and binds it into structlog contextvars so every
log line in the request scope is correlatable. The ID is echoed back in the
response header.

### Access Log Middleware ([app/api/middleware.py](../apps/backend/app/api/middleware.py))
Emits one `http_request` structlog event per request with method, path, status
code, and `perf_counter`-measured duration in milliseconds. Runs inside the
request-id middleware so each line already carries the request ID.

### CORS ([app/api/middleware.py](../apps/backend/app/api/middleware.py))
Standard FastAPI `CORSMiddleware` with origins driven by the `CORS_ORIGINS`
setting (JSON-parsed list). Credentials are enabled and all methods/headers are
allowed — adjust for stricter production needs.

### Health & Readiness Endpoints ([app/api/health_router.py](../apps/backend/app/api/health_router.py))
`GET /health` is a pure liveness probe returning `{"status":"ok"}`. `GET /ready`
is gated on a TTL-cached Ollama probe (PDFX-E007-F001): returns
`{"status":"ready"}` (200) when Ollama is reachable within the TTL window,
or `{"status":"not_ready","reason":"ollama_unreachable"}` (503) otherwise.

## Backend — Error System

### DomainError Hierarchy ([app/exceptions/base.py](../apps/backend/app/exceptions/base.py))
Single `DomainError` base class carrying a `code: ClassVar[str]` and
`http_status: ClassVar[int]`, plus an optional typed Pydantic `params` model.
Only the code is stored in `args` so PII in params never accidentally ends up
in stack traces.

### Generated Error Classes ([app/exceptions/_generated/](../apps/backend/app/exceptions/_generated/))
Every error code in `errors.yaml` is code-generated into its own Python file
with a typed params model (where applicable), enforcing the one-class-per-file
rule. A `_registry.py` maps error code strings back to classes for handler
lookup. The post-bootstrap shell ships three generic codes: `NOT_FOUND`,
`VALIDATION_FAILED`, `INTERNAL_ERROR`. Extraction-specific codes
(skill lookup, PDF parsing, intelligence layer, API layer) are added as
feature-dev lands the corresponding features.

### Exception Handlers ([app/api/errors.py](../apps/backend/app/api/errors.py))
Three handlers serialize `DomainError`, `RequestValidationError`, and unhandled
`Exception` into the same `{error: {code, params, details, request_id}}`
envelope. This guarantees every error the client sees is shape-identical
regardless of where it originated.

### Error Contracts Package ([packages/error-contracts/](../packages/error-contracts/))
Single source of truth: `errors.yaml` drives a Python codegen step (classes +
registry). Adding an error is one YAML edit plus `task errors:generate`.

## Backend — Response Shapes

### Error Response Schemas ([app/schemas/](../apps/backend/app/schemas/))
`ErrorDetail`, `ErrorBody`, and `ErrorResponse` split across three files (one
class each) to satisfy the sacred one-class-per-file rule. These are used only
for OpenAPI documentation; runtime error bodies are constructed by the
exception handlers directly.

## Backend — Architecture Enforcement

### Import-Linter Contracts ([apps/backend/architecture/import-linter-contracts.ini](../apps/backend/architecture/import-linter-contracts.ini))
The shipped contract set includes a pre-existing `shared-no-features`
contract; PDFX-E007-F004 added C1-C6 and related enforcement:

- `shared-no-features` (predates PDFX-E007-F004): `app/shared/`, `app/core/`,
  and `app/schemas/` cannot import from `app/features/`.
- `C1` feature independence (placeholder): documents the intent that the
  extraction feature cannot reach into sibling features. Vacuously satisfied
  today with a single feature module; related cross-feature import checks
  live in the AST-scan test
  [test_dynamic_import_containment.py](../apps/backend/tests/unit/architecture/test_dynamic_import_containment.py),
  but this placeholder contract does not itself claim dynamic sibling-feature
  enforcement (its `_collect_dynamic_import_targets()` helper currently returns
  only the root module of each `importlib.import_module` call, so dynamic
  sibling-feature imports are not flagged by the C1 check today).
- `C2a-C2e` intra-feature layer DAG: narrow `forbidden` and `independence`
  contracts that encode the extraction feature's non-linear subpackage DAG
  (leaves independent and non-upward; `extraction` may not import
  `coordinates` or `annotation`; `coordinates` may not import `annotation`,
  `intelligence`, or `skills`; `app/features/extraction/schemas/` is the
  base with no sibling imports).
- `C3` Docling containment: `docling` may only be imported in
  `app/features/extraction/parsing/docling_document_parser.py`.
- `C4` PyMuPDF containment: `pymupdf` and its `fitz` alias may only be
  imported in `app/features/extraction/annotation/pdf_annotator.py` and
  `app/features/extraction/parsing/docling_document_parser.py`
  (password-detection preflight).
- `C5` LangExtract containment: `langextract` may only be imported in
  `app/features/extraction/extraction/extraction_engine.py` and
  `app/features/extraction/intelligence/ollama_gemma_provider.py` (the
  community provider plugin entry point).
- `C6` httpx containment: `httpx` may only be imported in
  `app/features/extraction/intelligence/ollama_gemma_provider.py` and
  `app/features/extraction/intelligence/ollama_health_probe.py`. The
  contract is broader than "the Ollama client" — both the provider and the
  readiness probe build their own `httpx.AsyncClient`, so the containment
  targets `httpx` itself.

`C3-C6` use `source_modules = app` so the entire backend (including
`app.api`, `app.core`, etc.) is covered, not just the extraction feature.
Dynamic imports and cross-feature imports that static analysis cannot see
are covered by AST-scan tests in
[test_dynamic_import_containment.py](../apps/backend/tests/unit/architecture/test_dynamic_import_containment.py).

## Backend — Tests

### Unit Tests ([apps/backend/tests/unit/](../apps/backend/tests/unit/))
Fast, dependency-free tests covering Settings, domain error construction, and
error-handler serialization. These run in well under 10 seconds as the primary
TDD feedback loop.

### Integration Tests ([apps/backend/tests/integration/](../apps/backend/tests/integration/))
In-process integration against the FastAPI ASGI app via `httpx.AsyncClient`.
No external services required (no database, no Ollama). Covers `/health`,
`/ready`, request-id propagation, and CORS. Extraction-endpoint integration
tests land during feature-dev.

### Contract Tests ([apps/backend/tests/contract/](../apps/backend/tests/contract/))
Validates the generated OpenAPI spec shape. Schemathesis-based assertions
against `/api/v1/extract` are added during PDFX-E006 feature-dev.

## Infrastructure

### Backend Dockerfile ([infra/docker/backend.Dockerfile](../infra/docker/backend.Dockerfile))
Two-stage build using `uv` from `ghcr.io/astral-sh/uv` in the builder for fast
dependency installs, runtime on `python:3.13-slim` as a non-root user. Includes
a `HEALTHCHECK` hitting `/health` so orchestrators detect broken containers.

### Docker Compose ([infra/compose/](../infra/compose/))
`docker-compose.yml` runs the backend service with hot reload. Ollama runs on
the host machine and is reached via `host.docker.internal:11434`. `docker-compose.prod.yml`
runs the production variant with `restart: always` and `workers=1`.

## CI/CD

### CI Workflow ([.github/workflows/ci.yml](../.github/workflows/ci.yml))
Two jobs: `backend-checks` (ruff + pyright + import-linter + pytest all levels
+ contract) and `error-contracts` (codegen + validator tests + diff check).

### Deploy Workflow ([.github/workflows/deploy.yml](../.github/workflows/deploy.yml))
Triggered on push to main, builds the backend Docker image tagged with the
commit SHA. Push and deploy steps are intentional TODO stubs for wiring to the
actual registry/cluster.

### Copilot Review & Dependabot ([.github/](../.github/))
Copilot is auto-requested as a PR reviewer via a workflow. Dependabot has three
ecosystems wired (pip × 2, github-actions), all weekly.

## Tooling

### Taskfile ([Taskfile.yml](../Taskfile.yml))
Single orchestration entry point with `dev`, `check` (lint → types → arch →
test → errors), all test levels, errors generation, and docker commands.

### Pre-commit Hooks ([.pre-commit-config.yaml](../.pre-commit-config.yaml))
Pre-commit: whitespace/EOF/yaml/json/large-file checks + ruff fix + ruff format.
Pre-push: pytest unit tests.

### Editor & VCS Config ([.editorconfig](../.editorconfig), [.gitattributes](../.gitattributes), [.tool-versions](../.tool-versions))
LF line endings everywhere, 4-space Python, generated files marked
`linguist-generated`, Python version pinned via `.tool-versions` so
`asdf`/`mise` users get a consistent environment on first clone.
