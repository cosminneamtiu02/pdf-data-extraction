# Conventions

Rules that govern how code is written in this repository. See `CLAUDE.md` for the
enforcement version. This document provides rationale.

## File Naming

| Context | Convention | Example |
|---|---|---|
| Python files | `snake_case.py` | `extraction_service.py` |
| Python classes | `PascalCase` + role suffix | `ExtractionService`, `SkillLoader` |
| Python functions | `snake_case` verbs | `get_by_id`, `load_skill` |
| Feature folders | `snake_case` | `features/extraction/` |

## Test Naming

| Context | Convention | Example |
|---|---|---|
| Python | `test_<unit>_<scenario>_<expected>` | `test_skill_loader_rejects_duplicate_versions` |

## Test File Location

- Backend unit: `tests/unit/` mirrors the source tree.
  `app/features/extraction/skills/skill_loader.py` -> `tests/unit/features/extraction/skills/test_skill_loader.py`.
- Backend integration: `tests/integration/` mirrors the source tree. In-process
  against the FastAPI ASGI app; no external services.
- Backend contract: `tests/contract/` — three files, one per contract
  slice: `test_schemathesis.py` (schemathesis conformance across every
  declared `/api/v1/extract` status code), `test_extract_contract.py`
  (shape assertions for the OpenAPI spec and `ErrorResponse` envelope),
  `test_degraded_contract.py` (degraded-mode `/health` + `/ready`
  response shapes with a stubbed Ollama probe). See
  [docs/testing.md](testing.md#3-contract-tests) for details.

## Pydantic Schemas

Schemas are defined per feature under `features/<feature>/schemas/`, one class
per file. Schemas never import models or repository types (there are no
SQLAlchemy models in this service). Conversion happens in the service layer.

## Error System

- Error codes defined in `packages/error-contracts/errors.yaml` (single source of truth).
- Codegen produces one file per error class in `exceptions/_generated/`.
- Never edit `_generated/` files directly. Edit `errors.yaml` and run
  `task errors:generate`.

## Dependencies

- Always use absolute latest versions.
- Close/delete Dependabot PRs that propose older versions.
- Every new dependency requires justification.

## Environment Variables

- All config via `pydantic-settings` in `core/config.py`.
- Every new env var added to both the `Settings` class and
  `apps/backend/.env.example` in the same commit.

## Skill YAMLs

- Skill YAMLs live at `apps/backend/skills/{name}/{version}.yaml`.
- Integer versions only. The `latest` alias resolves to the highest integer.
- Every YAML is validated at container startup; a broken skill kills the boot.
- See PDFX-E002 feature specs in [`docs/graphs/PDFX/`](graphs/PDFX/) for the
  full skill authoring contract.
