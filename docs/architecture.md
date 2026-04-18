# Architecture

## System Overview

```
                    +--------------+
                    | Caller code  |    Any HTTP client in any language
                    | (integrator) |
                    +------+-------+
                           |
                  POST /api/v1/extract
                  multipart: pdf, skill, mode
                           |
                    +------v-------+
                    |  Extraction  |    FastAPI, single in-flight request
                    |   service    |    workers=1, stateless, synchronous
                    +------+-------+
                           |
         +-----------------+----------------+
         |                 |                |
    +----v----+    +-------v------+    +----v-----+
    | Docling |    |  LangExtract |    | PyMuPDF  |
    | (parse) |    | (orchestrate)|    |(annotate)|
    +---------+    +-------+------+    +----------+
                           |
                    +------v-------+
                    |    Ollama    |    External to the service container,
                    |   (Gemma 4   |    runs on the host machine.
                    |   smallest)  |
                    +--------------+
```

The only external runtime dependency is **Ollama** running on the host, serving the smallest Gemma 4 variant. Everything else is in-process Python.

## Backend Architecture: Vertical Slices

```
apps/backend/app/
├── core/               Config, logging. Cross-cutting infrastructure.
├── api/                 Middleware, exception handler, health/ready, shared deps.
├── exceptions/          DomainError base (base.py) + generated subclasses (_generated/).
├── shared/              Feature-agnostic helpers (reserved for base classes when needed).
├── schemas/             Response envelopes (ErrorBody, ErrorDetail, ErrorResponse).
└── features/
    └── extraction/     One folder per feature. Self-contained vertical slice.
                        (Subpackages land as feature-dev implements PDFX-E002+)
```

The extraction feature is built out during feature-dev per the graph tree in
[`docs/graphs/PDFX/`](graphs/PDFX/). Epics E002–E006 each map to one or more
subpackages inside `app/features/extraction/`. Epic E007 is cross-cutting —
its sub-features land in supporting locations (`app/api/*`, `app/core/*`,
`apps/backend/architecture/`, `apps/backend/scripts/`) rather than inside the
extraction feature. The table below reflects this:

| Epic | Files / subpackages | Responsibility |
|---|---|---|
| PDFX-E002 | `app/features/extraction/skills/` | Skill YAML loader, manifest, validation |
| PDFX-E003 | `app/features/extraction/parsing/` | Docling wrapper, `TextBlock` abstraction |
| PDFX-E004 | `app/features/extraction/intelligence/` + `app/features/extraction/extraction/` | LangExtract + Ollama provider + structured output validator |
| PDFX-E005 | `app/features/extraction/coordinates/` | Offset index, sub-block matcher, span resolver |
| PDFX-E006 | `app/features/extraction/schemas/`, `app/features/extraction/service.py`, `app/features/extraction/router.py`, `app/features/extraction/annotation/` | API surface + annotation |
| PDFX-E007-F001 | `app/features/extraction/intelligence/ollama_health_probe.py` + `app/api/health_router.py` | Ollama readiness probe wired into `GET /ready` |
| PDFX-E007-F002 | `app/api/middleware.py` | Request-ID + access-log + CORS middleware stack |
| PDFX-E007-F003 | `app/core/logging.py` | Structlog pipeline (contextvars, ISO timestamps, JSON/console) |
| PDFX-E007-F004 | `apps/backend/architecture/import-linter-contracts.ini` + AST-scan tests | Architectural quality gates (C1-C6 + dynamic-import scan) |
| PDFX-E007-F005 | `apps/backend/scripts/benchmark.py` | Extraction pipeline benchmarking harness |

### Layer Flow

```
HTTP Request
    |
    v
router.py       Thin handler. Parses multipart, size-checks, calls service, serializes response.
    |
    v
service.py       ExtractionService. Orchestrates the pipeline under asyncio.timeout(180s).
    |
    v
{parsing → coordinates → intelligence+extraction → annotation}
    |
    v
Response        JSON_ONLY / PDF_ONLY / multipart/mixed BOTH
```

No layer skipping. Router never touches Docling, LangExtract, Ollama, or PyMuPDF
directly — those imports are contained to specific implementation files via
`import-linter` contracts.

### Architectural Contracts (PDFX-E007-F004)

The full contract set lives in
[`apps/backend/architecture/import-linter-contracts.ini`](../apps/backend/architecture/import-linter-contracts.ini)
and is enforced by `task check:arch`, a direct dependency of `task check`
(issue #215; CI in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)
runs the same gates as `task check` via individual workflow steps, and
`task lint` itself is ruff-only). Every contract carries a `#` comment
block explaining the rule it encodes and why.

Two enforcement layers work together:

1. **import-linter** (static graph) -- the INI contracts (C1-C6) catch static
   `import X` / `from X import Y` violations at build time via `lint-imports`.
2. **AST-scan tests** (`test_dynamic_import_containment.py`) -- catch
   `importlib.import_module("X")` dynamic imports and cross-feature imports
   that import-linter's static analysis cannot see.

The C3-C6 third-party containment contracts use `source_modules = app` (the
entire app, not just the extraction feature) so that `app.api`, `app.core`,
and every other module are also forbidden from importing Docling, PyMuPDF,
LangExtract, or httpx outside the designated files.

C3 (Docling) permits three files inside `app/features/extraction/parsing/`:
`docling_document_parser.py` (the public coordinator),
`_real_docling_converter_adapter.py` (lazy-imports Docling to build the real
`DocumentConverter`), and `_real_docling_document_adapter.py` (wraps
`DoclingDocument`). The boundary expanded from a single filename to the
package after issue #159 split the parser to honor CLAUDE.md's
one-class-per-file rule; every Docling-touching line still lives behind
exactly these three allow-listed files.

C1 (feature independence) is an `independence` contract with a single module,
which is vacuously true in import-linter. The real enforcement is the AST
scan in `test_dynamic_import_containment.py::test_extraction_does_not_import_from_sibling_features`.
When a second feature package is introduced, the C1 contract must be amended
to include the new module so import-linter also becomes load-bearing.

The C2 DAG contracts (C2a-C2e) are decomposed into multiple narrow `forbidden`
and `independence` contracts instead of a single `layers` contract because the
mid-tier subpackages have asymmetric cross-edges that a single layers contract
cannot express cleanly.

### Error Flow

```
Any layer raises a DomainError subclass from errors.yaml
    |
    v
Exception handler serializes to JSON: {error: {code, params, details, request_id}}
    |
    v
Caller receives a machine-readable error with a stable `code`
```

All errors are defined in `packages/error-contracts/errors.yaml` and code-generated
via `task errors:generate`. Never raise `HTTPException`; never edit `_generated/`
directly.

## API Versioning

- Extraction endpoint: `POST /api/v1/extract` (multipart form).
- Health/readiness: `GET /health`, `GET /ready` (root, unversioned — orchestrator-facing).

## Packages

- `packages/error-contracts/` — `errors.yaml` source of truth plus Python codegen.
  The TypeScript codegen output (`src/generated.ts`) is still produced for future
  consumers but has no current caller.

## Infrastructure

- `infra/compose/` — docker-compose for dev and prod. Single backend service;
  Ollama is expected to run on the host and is reached via `host.docker.internal`.
- `infra/docker/` — Dockerfile for the backend service.
