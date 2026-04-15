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

### Architectural Contracts (PDFX-E007-F004)

The full contract set lives in
[`apps/backend/architecture/import-linter-contracts.ini`](../apps/backend/architecture/import-linter-contracts.ini)
and is enforced by `task check:arch` (which `task check` runs as part of every
PR's gate). Every contract carries a `#` comment block explaining the rule it
encodes and why. The high-level set:

| Contract | Rule | Type |
|---|---|---|
| `shared-no-features` | `app.shared`, `app.core`, `app.schemas` may not import from `app.features` (template legacy, preserved). | `forbidden` |
| **C1** `c1-feature-independence` | The `app.features.extraction` feature may not import from any sibling feature. Vacuously true today (only one feature exists); becomes load-bearing when a second feature lands. | `independence` |
| **C2a** `c2a-extraction-layers-leaves-independent` | `parsing`, `intelligence`, `skills`, `annotation` are leaf subpackages — they may not import each other. One time-bounded carve-out: `parsing.docling_config_merger -> skills.skill_docling_config` while the merger awaits its future home in the service layer. | `independence` |
| **C2b** `c2b-extraction-layers-leaves-no-upward` | The four leaf subpackages may not import from `coordinates` or `extraction` — i.e. no upward imports across the DAG. | `forbidden` |
| **C2c** `c2c-extraction-layers-extraction-subpackage` | The `extraction` subpackage (LangExtract orchestration) may import `parsing`, `intelligence`, `skills`, `schemas`, but never `coordinates` or `annotation`. | `forbidden` |
| **C2d** `c2d-extraction-layers-coordinates-subpackage` | The `coordinates` subpackage (span resolver) may import `parsing`, `extraction`, `schemas`, but never `annotation`, `intelligence`, or `skills`. | `forbidden` |
| **C2e** `c2e-extraction-layers-schemas-base` | `schemas` is the base layer — it may not import from any sibling subpackage. | `forbidden` |
| **C3** `c3-docling-containment` | `docling` may only be imported in `parsing/docling_document_parser.py`. Today the parser uses lazy `importlib.import_module`, so no static carve-out is needed; the contract becomes load-bearing the moment a contributor adds `import docling` anywhere. | `forbidden` |
| **C4** `c4-pymupdf-containment` | `pymupdf` and its legacy alias `fitz` may only be imported in `annotation/pdf_annotator.py`. | `forbidden` |
| **C5** `c5-langextract-containment` | `langextract` may only be imported in `extraction/extraction_engine.py` and `intelligence/ollama_gemma_provider.py` (the LangExtract community provider plugin registration site). | `forbidden` |
| **C6** `c6-httpx-containment` | `httpx` may only be imported in the `intelligence` subpackage (today: `ollama_gemma_provider.py`; future: `ollama_health_probe.py` will join naturally because the whole subpackage is the allowed site). | `forbidden` |

The DAG is decomposed into multiple narrow `forbidden` and `independence`
contracts (C2a–C2e) instead of a single `layers` contract because the
mid-tier subpackages have asymmetric cross-edges — `coordinates → extraction`
is allowed but `extraction → coordinates` is not, which a single layers
contract can't express cleanly. The Open Questions section of PDFX-E007-F004
defaults this decomposition explicitly: "use the right type per rule."

The contracts are scoped exclusively to `app.features.extraction.*`, so a
hypothetical future `PDFX-E008` feature that adds `app.features.<other>` can
land without editing this file. C1 catches accidental cross-feature imports
mechanically.

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
