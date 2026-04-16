---
date: 2026-04-13
skill: project-bootstrap
branch: chore/pdfx-bootstrap
status: complete
---

# Bootstrap Decisions — PDFX (2026-04-13)

Record of the `project-bootstrap` skill run that stripped this repository
from a full-stack monorepo template into a minimal shell for the PDF Data
Extraction Microservice (`PDFX`). The strip was driven by the graph tree at
[`docs/graphs/PDFX/`](graphs/PDFX/) and the design + requirements specs at
[`docs/superpowers/specs/`](superpowers/specs/).

## Decision Log

### Autonomous strips

| # | Capability | Source |
|---|---|---|
| 1 | `apps/frontend/` entire directory | autonomous |
| 2 | `packages/api-client/` entire directory | autonomous |
| 3 | `infra/terraform/` entire directory | autonomous |
| 4 | `apps/backend/alembic/` + `alembic.ini` | autonomous |
| 5 | `apps/backend/app/features/widget/` entire directory | autonomous |
| 6 | `apps/backend/app/core/database.py` | autonomous |
| 7 | `apps/backend/app/shared/base_repository.py` | autonomous |
| 8 | `apps/backend/app/shared/base_model.py` | autonomous |
| 9 | `apps/backend/app/shared/base_service.py` (per OQ-005) | autonomous |
| 10 | `apps/backend/app/types/` entire directory (money, currency) | autonomous |
| 11 | `apps/backend/app/schemas/page.py` | autonomous |
| 12 | All widget tests (unit + integration) | autonomous |
| 13 | Testcontainers setup (`tests/integration/conftest.py`) | autonomous |
| 14 | Testcontainers-based tests (`shared/test_base_*.py`, `test_rollback_canary.py`) | autonomous |
| 15 | `infra/docker/frontend.Dockerfile` + `frontend.dev.Dockerfile` | autonomous |
| 16 | `infra/docker/nginx.conf` | autonomous |

### User-confirmed strips

| # | Capability | Source |
|---|---|---|
| 17 | Security headers middleware (`SecurityHeadersMiddleware`) | user-answered — "fully local, can be discarded" |
| 18 | Root JS workspace: `pnpm-workspace.yaml`, `pnpm-lock.yaml`, `node_modules/` | user-answered — "strip" |
| 19 | Node + pnpm pins from `.tool-versions` | follows from #18 |
| 20 | Biome hook from `.pre-commit-config.yaml` | follows from #1 + #18 |
| 21 | `frontend-checks` job from `.github/workflows/ci.yml` | follows from #1 |
| 22 | `api-client-checks` job from `.github/workflows/ci.yml` | follows from #2 |
| 23 | npm / pnpm Dependabot ecosystems | follows from #18 |
| 24 | terraform Dependabot ecosystem | follows from #3 |
| 25 | `packages/error-contracts/package.json` + `src/generated.ts` | follows from #18 (no JS consumer left; generated.ts is still regenerated idempotently by the codegen) |
| 26 | Root `.env.example` frontend/DB sections | follows from #1 + DB strip |

### User-confirmed keeps

| Capability | Reason |
|---|---|
| CORS middleware | User answered "keep" — downstream callers may be in separate processes |
| Copilot PR review workflow | User answered "keep" |
| Request-ID middleware | Required by PDFX-E007-F003 |
| Access Log middleware | Required by PDFX-E007-F003 |
| Dependabot + auto-merge workflow + lockfile sync | CLAUDE.md sacred |
| All pre-commit / pre-push Python hooks | CLAUDE.md sacred |
| Error contracts package (`errors.yaml`, codegen, tests) | Required for extraction features' error codes |
| `import-linter` + `architecture/` directory | Required by PDFX-E007-F004 |
| Health router (`/health` + `/ready`) | Required; `/ready` now Ollama-probe-gated (PDFX-E007-F001) |
| Exception handlers (`app/api/errors.py`) | Unchanged — feature-agnostic |
| Error body schemas (`app/schemas/error_*.py`) | Unchanged — feature-agnostic |
| Three generic error codes (`NOT_FOUND`, `VALIDATION_FAILED`, `INTERNAL_ERROR`) | Generic and needed by future features — `CONFLICT` pruned in PDFX-E001-F004 as a CRUD-era orphan |
| `.editorconfig`, `.gitattributes`, Python pin in `.tool-versions` | Default keeps |

### Partials (kept but significantly rewritten)

| # | File | Change |
|---|---|---|
| 27 | `apps/backend/pyproject.toml` | Dropped `sqlalchemy`, `asyncpg`, `alembic`, `testcontainers[postgres]`; kept the rest. Regenerated `uv.lock`. |
| 28 | `apps/backend/app/main.py` | Removed database lifespan, removed widget router mount. Simplified app factory. |
| 29 | `apps/backend/app/core/config.py` | Removed `database_url` and the postgres validator. Kept `app_env`, `log_level`, `cors_origins`. |
| 30 | `apps/backend/app/api/middleware.py` | Removed `SecurityHeadersMiddleware`. Kept request-id, access log, CORS. |
| 31 | `apps/backend/app/api/health_router.py` | Removed DB probe from `/ready`; now Ollama-probe-gated readiness (PDFX-E007-F001). |
| 32 | `apps/backend/app/exceptions/__init__.py` | Removed widget + rate-limited re-exports. |
| 33 | `apps/backend/app/exceptions/_generated/` | Removed widget_* + rate_limited_* files; regenerated cleanly via `task errors:generate`. |
| 34 | `apps/backend/architecture/import-linter-contracts.ini` | Removed 4 widget-specific contracts; kept the `shared-no-features` contract. Full extraction-feature contracts land in PDFX-E007-F004. |
| 35 | `apps/backend/tests/unit/core/test_config.py` | Rewrote to test defaults and overrides (no `DATABASE_URL`). |
| 36 | `apps/backend/tests/unit/exceptions/test_domain_errors.py` | Switched from `WidgetNotFoundError` to `ValidationFailedError`. |
| 37 | `apps/backend/tests/unit/exceptions/test_error_handler.py` | Switched widget test triggers to `NotFoundError` + `ValidationFailedError`. |
| 38 | `apps/backend/tests/integration/test_health.py` | Rewrote to run in-process against the ASGI app via `httpx.AsyncClient` + `ASGITransport`. Removed DB mock tests and security-headers tests. |
| 39 | `apps/backend/tests/contract/test_schemathesis.py` | Removed widget path assertions and `DATABASE_URL` env setup. |
| 40 | `packages/error-contracts/errors.yaml` | Removed `WIDGET_*` + `RATE_LIMITED`; kept generics. |
| 41 | `packages/error-contracts/src/required-keys.json` | Regenerated by `task errors:generate`. |
| 42 | `Taskfile.yml` | Removed frontend tasks, DB tasks, client:generate, storybook tasks, errors:check (no locales), test:e2e. Kept `check`, `test:*`, `lint`, `format`, `errors:generate`, `docker:*`. |
| 43 | `infra/compose/docker-compose.yml` + `docker-compose.prod.yml` | Removed `db` and `frontend` services. Backend service remains. Added `host.docker.internal:host-gateway` for Linux-host Ollama reachability. |
| 44 | `.github/workflows/ci.yml` | Kept `backend-checks` and `error-contracts` jobs only. Removed Postgres service. Removed `DATABASE_URL` env. |
| 45 | `.github/workflows/deploy.yml` | Removed frontend image build step. |
| 46 | `.github/dependabot.yml` | Removed npm + pnpm ecosystems (frontend + api-client). Removed terraform ecosystem. Kept pip × 2 + github-actions. Pruned stale group patterns. |
| 47 | `.pre-commit-config.yaml` | Removed Biome hook + vitest-unit hook. Kept ruff + pytest unit. |
| 48 | `.tool-versions` | Removed Node + pnpm pins; kept Python pin. |
| 49 | `.env.example` | Removed `DATABASE_URL`. Kept optional defaults. |
| 50 | `.gitignore` | Removed Node/Vite/Storybook/Playwright/Terraform patterns; kept Python patterns. |
| 51 | `README.md` | Rewritten for the extraction microservice. |
| 52 | `CLAUDE.md` | Rewrote project overview, stack section; removed Frontend forbidden patterns and DB-specific backend forbidden patterns; added extraction-specific rules placeholder; testing section updated to 3 levels; Dependabot section preserved. |
| 53 | `docs/architecture.md` | Rewritten for the extraction service pipeline. |
| 54 | `docs/conventions.md` | Rewritten. Removed frontend conventions, Money/SQLAlchemy/Alembic sections. |
| 55 | `docs/decisions.md` | Removed ADR-002 / 006 / 007 / 008 (superseded). Added ADR-011 documenting the bootstrap run. Preserved ADR-010 (Dependabot auto-merge). |
| 56 | `docs/testing.md` | Rewritten to 3 levels (unit / integration / contract) + optional slow E2E. |
| 57 | `docs/runbook.md` | Rewritten with Ollama operator notes and updated command list. |
| 58 | `docs/new-project-setup.md` | Targeted edits: 2 check names instead of 4, Node/pnpm removed from prerequisites, frontend/db phases removed, Python-only dependabot narrative, uv lockfile manual fallback. |
| 59 | `docs/ai-guide.md` | Rewritten for the post-bootstrap shell. |
| 60 | `docs/features.md` | Rewritten to list only the kept capabilities. |
| 61 | `TEMPLATE_FRICTION.md` | Appended a 2026-04-13 bootstrap-run entry. |

## Execution Order

1. Bulk deletion of directories (frontend, api-client, terraform, alembic, widget, etc.) and root JS workspace files.
2. Deletion of individual leaf files (database.py, base_*.py, page.py, money.py, currency.py, frontend dockerfiles, nginx.conf).
3. Deletion of widget tests and DB-dependent integration tests and conftest.
4. Rewrite of source files (`main.py`, `config.py`, `middleware.py`, `health_router.py`, `exceptions/__init__.py`, `_generated/__init__.py`, `_generated/_registry.py`).
5. Rewrite of `errors.yaml` and running `task errors:generate` (regenerated Python classes, generated.ts, required-keys.json).
6. Rewrite of backend test files (`test_config.py`, `test_domain_errors.py`, `test_error_handler.py`, `test_health.py`, `test_schemathesis.py`).
7. Rewrite of build/lint configs (`pyproject.toml`, `Taskfile.yml`, `import-linter-contracts.ini`, `.pre-commit-config.yaml`).
8. Rewrite of infra configs (`docker-compose.yml`, `docker-compose.prod.yml`, `.github/workflows/ci.yml`, `deploy.yml`, `dependabot.yml`).
9. Rewrite of meta-files (`.tool-versions`, `.env.example`, `.gitignore`).
10. Rewrite of discipline and documentation files (`CLAUDE.md`, `README.md`, and every file under `docs/` except `superpowers/specs/` and `graphs/PDFX/`).
11. Appended the bootstrap entry to `TEMPLATE_FRICTION.md`.
12. Ran `uv lock` + `uv sync --dev` to regenerate the backend lockfile.
13. Ran `task errors:generate` one more time to confirm idempotence.
14. Ran Phase 4 verification.

## Verification Iterations

**Iteration 1 — all green.** No fixes needed.

| Step | Result |
|---|---|
| `uv lock` (apps/backend) | Removed 9 DB-stack packages; resolved 65 total. |
| `uv sync --dev` (apps/backend) | Clean install of the trimmed tree. |
| `task errors:generate` | Generated all error contract files. |
| `uv run ruff check .` | All checks passed. |
| `uv run ruff format --check .` | 36 files already formatted. |
| `uv run pyright app/` | 0 errors, 0 warnings, 0 informations. |
| `uv run lint-imports --config architecture/import-linter-contracts.ini` | 1 contract kept, 0 broken. |
| `uv run pytest tests/unit/ -v` | 8 passed. |
| `uv run pytest tests/integration/ -v` | 5 passed. |
| `uv run pytest tests/contract/ -v` | 2 passed. |
| error-contracts package: `uv run --with pytest --with pyyaml pytest tests/ -v` | 12 passed. |
| `task check` (aggregate) | Fully green on the first run. |

## Summary

- **Autonomous strips:** 16
- **User-confirmed strips:** 10 (plus cascading consequences)
- **Kept-with-modifications files:** 35
- **Deleted files / directories:** ~200 (approximate; includes the full frontend tree, api-client, terraform, alembic, widget slice, widget tests, etc.)
- **Verification iterations:** 1 (all green)
- **Test suite after bootstrap:** 27 tests total (8 unit + 5 integration + 2 contract + 12 error-contracts)
- **Working tree state:** dirty and uncommitted. The user reviews and commits manually.

## Next Steps

The post-bootstrap shell is ready for feature-dev. The 29 thickened features
in [`docs/graphs/PDFX/`](graphs/PDFX/) are laid out in topological priority
order starting at PDFX-E002-F001 (PDFX-E001 is this bootstrap run, now done).
The design spec at
[`docs/superpowers/specs/2026-04-13-pdf-extraction-microservice-design.md`](superpowers/specs/2026-04-13-pdf-extraction-microservice-design.md)
and the requirements spec at
[`docs/superpowers/specs/2026-04-13-pdf-extraction-microservice-requirements.md`](superpowers/specs/2026-04-13-pdf-extraction-microservice-requirements.md)
are the authoritative technical references for the build-out.
