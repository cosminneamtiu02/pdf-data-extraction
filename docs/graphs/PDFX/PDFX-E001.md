---
type: epic
id: PDFX-E001
parent: PDFX
title: Template Cleanup & Refactor Preparation
status: fully-detailed
priority: 10
dependencies: []
---

## Short description

Strip the existing monorepo template down to what the PDF extraction microservice actually needs — remove the widget CRUD vertical slice, the database layer, Alembic, the frontend, the generated api-client package, and the CRUD-specific error codes — so the new extraction vertical slice can be built on a clean backend shell.

## Description

The repository is a monorepo template that currently ships a widget CRUD example built on FastAPI + SQLAlchemy + Postgres + React + TanStack. The PDF extraction microservice has no database, no frontend, no persistent storage, and no client application. This Epic performs the one-shot cleanup that removes everything not needed for the extraction feature, adjusts `pyproject.toml`'s dependency list to drop the database-related packages (`sqlalchemy`, `alembic`, `asyncpg`) and add the extraction-related ones (`docling`, `langextract`, `pymupdf`, `jsonschema`, `httpx`, `pyyaml`), updates `infra/compose/docker-compose.yml` to remove the postgres and frontend services, prunes the `errors.yaml` contract of all CRUD-specific error codes, removes `apps/frontend/` and `packages/api-client/` entirely, removes the Alembic migrations directory, and re-runs `task errors:generate` so the generated Python and TypeScript error artifacts reflect the new shape. Template infrastructure — Dependabot auto-merge workflow, pre-commit config, CI, Taskfile, the error-contract codegen flow, and the four-level testing discipline — is preserved unchanged.

## Rationale

This Epic exists because the extraction feature cannot coexist with the template's database and frontend layers: the architectural rules in CLAUDE.md forbid certain patterns that the template's widget example uses (e.g. `BaseRepository`, `BaseModel`), the `import-linter` contracts would fail on any mixed state, and the `task check` gate needs a clean slate before new vertical-slice code arrives. It traces to the Project success criterion **"`task check` passes cleanly on every PR"** — without the cleanup, the first PR that touches the new extraction code would drag along unrelated failing tests from the removed layers. It also traces to **CLAUDE.md compliance**: the template's forbidden-patterns list is extended during this Epic to add extraction-specific rules (e.g. "Never bypass `StructuredOutputValidator`", "Never hardcode an Ollama model tag in source"), which cannot land incrementally because CLAUDE.md is read at every session start.

## Boundary

**In scope:** removing existing template artifacts (widget feature, database layer, Alembic, frontend, api-client package, CRUD error codes); adjusting `pyproject.toml`, `docker-compose.yml`, `import-linter-contracts.ini`, `Taskfile.yml`, `docs/architecture.md`, and `CLAUDE.md` to match the post-cleanup reality; re-running `task errors:generate` against the trimmed `errors.yaml`; verifying `task check` still passes against the minimal remaining FastAPI shell.

**Out of scope:** any new extraction-feature code (that begins in PDFX-E002 and beyond); any changes to Dependabot, CI workflows, pre-commit config, or the Taskfile's core structure; any addition of the `skills/` data directory (that lives in PDFX-E002).

## Implementation note (post-hoc)

This Epic was delivered as commit `f4813d1` ("chore: strip template to PDFX extraction microservice shell"), which performed the entire cleanup as one consolidated sweep rather than five sequential PRs. The strip went further than the original boundary statement implied: the root-level `pnpm 10` + Node 22 workspace shell (`package.json`, `pnpm-workspace.yaml`, `pnpm-lock.yaml`) was also removed, since the extraction service is Python-only and the surviving JavaScript shell would have been an empty husk with no artifacts to manage. The CI, Dependabot auto-merge, pre-commit, and Taskfile runner — the parts of the template that are actually load-bearing for the Python service — were all preserved unchanged. See [PDFX-E001-F003](PDFX-E001-F003.md)'s implementation note for the reconciliation rationale.

## Open questions

*This list is not exhaustive. Additional questions may surface during feature elicitation.*

- **OQ-005** — Whether `app/shared/base_service.py` survives the cleanup. Depends on whether its current interface is database-coupled. Default action if unresolved: remove it; `ExtractionService` will be a plain class.
- Whether the `app/types/` directory (`money.py`, `currency.py`) needs to stay for any non-extraction reason. Default: remove — these are widget-era value objects.
- Whether the `app/schemas/page.py` pagination envelope is removed entirely or kept for a hypothetical future paginated endpoint. Default: remove — the extraction service has no paginated endpoints.
