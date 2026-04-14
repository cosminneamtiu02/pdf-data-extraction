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
[`docs/graphs/PDFX/`](graphs/PDFX/). Each Epic maps to one or more subpackages
inside `features/extraction/`:

| Epic | Subpackage | Responsibility |
|---|---|---|
| PDFX-E002 | `skills/` | Skill YAML loader, manifest, validation |
| PDFX-E003 | `parsing/` | Docling wrapper, `TextBlock` abstraction |
| PDFX-E004 | `intelligence/` + `extraction/` | LangExtract + Ollama provider + structured output validator |
| PDFX-E005 | `coordinates/` | Offset index, sub-block matcher, span resolver |
| PDFX-E006 | `schemas/`, `service.py`, `router.py`, `annotation/` | API surface + annotation |
| PDFX-E007 | `app/api/health_router.py` + `middleware.py` + `import-linter-contracts.ini` | Platform, observability, quality gates |

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
