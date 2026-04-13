# Reshape Plan

> Single source of truth for the template monorepo reshape.
> Produced in a planning session. No code changes accompany this file.
> The execution session that follows this plan treats this document as authoritative.

---

## 1. Executive Summary

This template monorepo is being rebuilt from a partially-scaffolded Angular + FastAPI skeleton into a production-ready foundation using React 19 (Vite, TypeScript strict, TanStack Query/Router, Zustand, Tailwind, shadcn/ui, Storybook, Biome) on the frontend and FastAPI (SQLAlchemy 2.0 async, Alembic, Pydantic v2, structlog) on the backend, with Postgres 17 as the database. The reshape introduces strict vertical-slice architecture enforced by import-linter, four test levels (unit, integration, E2E, contract) all demonstrated on a single `Widget` CRUD example built TDD-first, a typed internationalized error system with build-time validation, and a `CLAUDE.md` discipline file that mechanically governs all future AI-assisted development. The entire reshape ships as one PR against `main`.

---

## 2. Discovery and Audit

### 2.1 Current Folder Tree

```
fe-be-repo-model/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   └── health_router.py          # GET /health → {"status": "ok"}
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   └── config.py                 # pydantic-settings: 2 unused API key fields
│   │   ├── domain/
│   │   │   └── __init__.py               # Empty — never used
│   │   ├── infrastructure/
│   │   │   ├── __init__.py
│   │   │   └── external_clients.py       # Dead code — generic httpx wrapper, never imported
│   │   ├── modules/
│   │   │   ├── __init__.py
│   │   │   └── example_module/
│   │   │       ├── __init__.py
│   │   │       ├── domain_model.py       # Dead code — Pydantic model, never imported by app
│   │   │       └── service.py            # Dead code — hardcoded dict returns, never imported by app
│   │   ├── services/
│   │   │   └── __init__.py               # Empty — never used
│   │   ├── __init__.py
│   │   └── main.py                       # FastAPI app, mounts health_router only
│   ├── architecture/
│   │   └── import-linter-contracts.ini   # 2 contracts enforcing rules on empty layers
│   ├── tests/
│   │   ├── __init__.py
│   │   ├── test_config.py                # Tests unused config fields
│   │   ├── test_domain_model.py          # Tests dead ExampleModel
│   │   ├── test_example_service.py       # Tests dead ExampleService
│   │   ├── test_external_clients.py      # Tests dead ExternalApiClient (fully mocked)
│   │   └── test_health.py               # Tests health endpoint — only valuable test
│   ├── debug.py                          # debugpy entrypoint — deleted
│   ├── Dockerfile                        # Multi-stage, python:3.12-slim, uv
│   ├── .dockerignore
│   ├── pyproject.toml
│   ├── README.md
│   └── uv.lock
├── frontend/                             # ENTIRE DIRECTORY DELETED
│   ├── dist/                             # Committed build artifacts
│   ├── storybook-static/                 # Committed build artifacts — 100k+ lines
│   ├── .storybook/
│   ├── src/app/
│   │   ├── core/{config,guards,interceptors,services}/  # All .gitkeep, never implemented
│   │   ├── features/example/             # Calls /api/examples — endpoint doesn't exist
│   │   └── shared/components/example-button/
│   ├── angular.json, karma.conf.js, eslint.config.js
│   ├── package.json                      # Angular 21
│   ├── package-lock.json, .npmrc, proxy.conf.json, nginx.conf
│   ├── tsconfig.json, tsconfig.spec.json, tsconfig.storybook.json
│   ├── Dockerfile
│   ├── .dockerignore
│   └── tests/.gitkeep
├── infrastructure/
│   └── terraform/                        # AWS-hardcoded, contradicts cloud-agnostic target
│       ├── main.tf
│       ├── modules/.gitkeep
│       └── environments/{dev,prod}/main.tf
├── docs/                                 # ALL STALE
│   ├── architecture.md                   # Says "Angular"
│   ├── ci-cd.md                          # References ng lint, npm test
│   ├── database-model.md                 # Claims SQLAlchemy is in use — it isn't
│   ├── development.md                    # Angular-centric, references deleted .ai/
│   ├── domain-model.md                   # References ExampleModule only
│   ├── new-project-setup.md              # References .ai/, JWT_SECRET, Angular ports
│   └── project-structure.md              # Lists .ai/ directory that doesn't exist
├── env-files/
│   └── .env.example                      # 4 phantom keys, none used
├── scripts/                              # ALL DELETED — replaced by Taskfile
│   ├── dev.sh, debug_backend.sh, debug_frontend.sh
│   ├── lint.sh, test.sh, replay_ci.sh
├── .github/
│   ├── dependabot.yml
│   └── workflows/
│       ├── ci.yml                        # Triggers on non-existent develop branch
│       ├── copilot-review.yml            # Keep
│       └── deploy.yml                    # Stub — echo "TODO"
├── .pre-commit-config.yaml               # Includes prettier (replaced by Biome)
├── .vscode/{settings.json, extensions.json}
├── docker-compose.dev.yml                # postgres:16, port 4200 (Angular)
├── docker-compose.prod.yml
├── Makefile                              # DELETED
├── .gitignore                            # Ghost entries for .claude/handoff, .ai/snapshots
├── package-lock.json                     # ORPHAN — no root package.json
├── CONTRIBUTING.md                       # References .ai/
├── README.md                             # References .ai/, says "Angular"
└── LICENSE
```

### 2.2 Backend Architecture Audit

**Layering: scaffolded, never implemented.** Folders exist (`api/`, `services/`, `domain/`, `infrastructure/`, `modules/`) but only `api/health_router.py` and `main.py` contain functional code. No route calls a service. No service calls a repository. No repository exists. The `modules/` pattern conflicts with the target architecture and is entirely disconnected — `ExampleService` and `ExampleModel` are never imported by any application code.

**Business logic:** none. The backend is a health endpoint.

**Response shape:** bare dict `{"status": "ok"}`. No envelopes. No `JSONResponse` subclass.

**Error handling:** none. No custom exceptions, no exception handlers, no `HTTPException` usage.

**Configuration:** `pydantic-settings` present with two unused fields (`openai_api_key`, `microsoft_api_key`). The `settings` singleton is never imported by any other module.

**Logging:** none.

**Middleware:** none. No CORS.

**Dependency injection:** none. No `Depends()` usage.

**SQLAlchemy:** listed as dependency, never imported. Zero database code.

### 2.3 Angular-Shaped Backend Decisions

**None exist.** The backend is too empty to have been shaped by Angular. No envelope responses, no CORS config, no `/api` prefix, no OpenAPI client generation tooling. The proxy config (`/api` → `localhost:8000`) references an `/api` prefix that no backend route uses. The frontend `ExampleService` calls `/api/examples` — an endpoint that does not exist. Frontend and backend were never integrated.

### 2.4 Test Inventory

**Backend — 5 test files, all unit-level:**

| File | Tests | Classification | Verdict |
|---|---|---|---|
| `test_health.py` | 1 | Unit (TestClient) | **Rewrite** |
| `test_config.py` | 2 | Unit (pure logic) | **Delete** — tests fields being removed |
| `test_domain_model.py` | 3 | Unit (Pydantic) | **Delete** — tests dead code |
| `test_example_service.py` | 3 | Unit (pure logic) | **Delete** — tests dead code |
| `test_external_clients.py` | 3 | Unit (fully mocked) | **Delete** — tests dead code |

No integration, E2E, or contract tests exist.

**Frontend — 2 spec files (Jasmine/Karma):** Angular TestBed boilerplate. Deleted with Angular.

### 2.5 Frontend Inventory

Angular 21, standalone components. One feature (example page), one shared component (example button), one Storybook story. Core folders all `.gitkeep`. `HttpClient` calls a non-existent endpoint. State management: none.

### 2.6 Documentation Audit

| File | Verdict | Reason |
|---|---|---|
| `docs/architecture.md` | **Delete, rewrite** | Wrong stack |
| `docs/ci-cd.md` | **Delete, rewrite** | Wrong CI shape |
| `docs/database-model.md` | **Delete, rewrite** | Claims SQLAlchemy in use |
| `docs/development.md` | **Delete, rewrite** | Angular-centric, references deleted `.ai/` |
| `docs/domain-model.md` | **Delete, rewrite** | References ExampleModule |
| `docs/new-project-setup.md` | **Delete, rewrite** | References `.ai/`, wrong tooling |
| `docs/project-structure.md` | **Delete, rewrite** | Lists `.ai/` that doesn't exist |
| `CONTRIBUTING.md` | **Delete, rewrite** | References `.ai/` |
| `README.md` | **Delete, rewrite** | Angular-centric |
| `backend/README.md` | **Delete, rewrite** | Minimal, wrong commands |

Every document is stale. All reference deleted infrastructure or wrong frameworks.

### 2.7 CI / Pre-commit Inventory

| CI Job | Post-reshape |
|---|---|
| `ci.yml` — `backend-tests` | **Rewrite** — Python 3.13, ruff, pyright strict, pytest, import-linter |
| `ci.yml` — `frontend-tests` | **Rewrite** — Node 22, pnpm, biome, tsc strict, vitest, playwright |
| `ci.yml` trigger | **Fix** — `[main]` only, `develop` doesn't exist |
| `copilot-review.yml` | **Keep** |
| `deploy.yml` | **Rewrite** — update paths, keep as stub |

| Pre-commit Hook | Post-reshape |
|---|---|
| `trailing-whitespace`, `end-of-file-fixer`, `check-yaml`, `check-json`, `check-added-large-files` | **Keep** |
| `ruff` + `ruff-format` | **Keep**, update rev |
| `prettier` | **Delete** — replaced by Biome |

### 2.8 Infra Inventory

- `docker-compose.dev.yml` — Postgres 16, Angular port 4200. Needs rewrite.
- `docker-compose.prod.yml` — same shape. Needs rewrite.
- `infrastructure/terraform/` — AWS-hardcoded. S3 backends with real bucket names. Missing `staging`. Contradicts cloud-agnostic target.
- Backend Dockerfile: multi-stage, python:3.12, uv. Needs version bump and non-root user.
- Frontend Dockerfile: Angular-specific. Deleted, rewritten for React.
- `frontend/dist/` and `frontend/storybook-static/` committed to git. Deleted from tree.
- Root `package-lock.json` orphaned. Deleted.

### 2.9 Phantom References

| Reference | Location | Status |
|---|---|---|
| `.ai/` directory (12 files) | README, CONTRIBUTING, docs | Deleted in commit `f216774` |
| `.claude/` directory | .gitignore | Deleted — lives in user's global config |
| `JWT_SECRET` | .env.example, docs | Never used in code |
| `DATABASE_URL` | .env.example, docs | Never used (no DB connection) |
| `openai_api_key` / `microsoft_api_key` | config.py | Never imported |
| `ExternalApiClient` | infrastructure/ | Never imported by app code |
| `ExampleService` | modules/ | Never imported by app code |
| `ExampleModel` | modules/ | Never imported by app code |
| `develop` branch | ci.yml | Branch doesn't exist |
| `/api/examples` endpoint | frontend service | Backend has no such endpoint |
| `/api` prefix | proxy.conf, nginx.conf | Backend uses no prefix |

---

## 3. Locked-In Decisions

### 3.1 Stack

| Layer | Choice |
|---|---|
| Python | 3.13, pinned in `.tool-versions` |
| Node | 22 LTS, pinned in `.tool-versions` |
| Python package manager | uv (latest) |
| Frontend package manager | pnpm 10, pinned in `.tool-versions` |
| Backend framework | FastAPI (latest) |
| Validation / schemas | Pydantic v2 (latest) |
| Configuration | pydantic-settings (latest) |
| ORM | SQLAlchemy 2.0 async (latest) |
| Migrations | Alembic (latest) |
| DB driver | asyncpg (latest) |
| Logging | structlog (latest) |
| Frontend framework | React 19 (latest) |
| Build tool | Vite (latest) |
| TypeScript | strict mode (latest) |
| Server state | TanStack Query (latest) |
| Routing | TanStack Router (latest) |
| Client state | Zustand (latest) |
| Styling | Tailwind CSS (latest) |
| Component library | shadcn/ui (latest) |
| Component docs | Storybook (latest) |
| i18n | i18next + react-i18next (latest) |
| Frontend money | dinero.js (latest) |
| Frontend lint/format | Biome (latest) + ESLint (i18next rule only) |
| Backend lint/format | Ruff (latest), `select = ["ALL"]` |
| Backend type checking | Pyright strict (latest) |
| Backend layer enforcement | import-linter (latest) |
| Backend testing | pytest + pytest-asyncio + Testcontainers + Schemathesis (latest) |
| Frontend unit testing | Vitest + React Testing Library (latest) |
| Frontend E2E testing | Playwright (latest) |
| API client generation | openapi-typescript + openapi-fetch (latest) |
| Database | PostgreSQL 17 |
| Task runner | Taskfile (latest) |
| Dev orchestration | docker-compose at `infra/compose/docker-compose.yml` |

### 3.2 Architecture

- **Backend:** vertical slices. `app/features/<feature>/` contains router, service, repository, model, and schemas. Shared abstractions in `app/shared/`. Cross-cutting concerns in `app/core/`.
- **Frontend:** vertical slices. `src/features/<feature>/` contains api hooks and components (each in own folder with co-located tests and stories). Shared code in `src/shared/`. App shell in `src/app/`. Routes in `src/routes/`.
- **Layer enforcement (backend):** import-linter. Features cannot import from other features. Within a feature: router → service → repository → model. Schemas never import models. Enforced in CI.
- **Layer enforcement (frontend):** Biome lint rules. Features cannot import from other features. Features can import from `shared/`. `shared/` cannot import from `features/`.
- **Response shape:** bare Pydantic models for single entities. `Page[T]` for lists (`items`, `total`, `page`, `size`, `pages`). Errors are `ErrorResponse` with `code`, `params`, `details`, `request_id`.
- **Pagination:** offset-based. `page` + `size` query params. `Page[T]` response.
- **Error handling:** `DomainError` hierarchy generated from `errors.yaml`. Single FastAPI exception handler maps to `ErrorResponse`. Route handlers never raise `HTTPException`.
- **i18n:** `i18next` + `react-i18next`. `/$lang/` URL prefix. Detection: localStorage → navigator.languages → default `en`. Error codes are i18n keys.
- **Metadata:** HTTP headers via middleware (request ID, security headers). Never in response bodies.
- **Logging:** structlog only. `print` and stdlib `logging` forbidden.
- **Configuration:** pydantic-settings, `.env` locally, env vars in production. Fail-fast on missing required config.
- **Auth:** none. Template is auth-less.
- **API versioning:** all business routes under `/api/v1/`. Health and readiness at root (`/health`, `/ready`).

### 3.3 Sacred Rules

1. **One class per file. Always.** No exceptions. If you believe two classes belong together, stop and ask.
2. **TDD. Always.** Never write implementation before a failing test exists for it.
3. **No paradigm drift.** One way to do each thing. If you think a second way is needed, stop and ask.

### 3.4 Conventions

- **Python files:** `snake_case.py`
- **Python classes:** `PascalCase` with role suffix (`WidgetService`, `WidgetRepository`, `WidgetCreate`, `WidgetRead`, `WidgetUpdate`)
- **Python functions:** `snake_case` verbs
- **Frontend files (all):** `kebab-case.tsx` / `kebab-case.ts`
- **Frontend component exports:** `PascalCase` (`export function WidgetList`)
- **Frontend hook exports:** `useCamelCase`
- **Frontend feature folders:** `kebab-case`
- **Each frontend component in its own folder:** `component-name/component-name.tsx`, `.test.tsx`, `.stories.tsx`
- **Test naming (Python):** `test_<unit>_<scenario>_<expected>`
- **Test naming (Vitest):** `describe("<Subject>", () => { it("<behavior>", ...) })`
- **Test naming (Playwright):** `test("<user-facing behavior>", ...)`
- **Migration files:** `<rev>_<slug>.py`, slug in `snake_case`, one logical change per migration
- **SQLAlchemy MetaData naming convention:** explicit constraint names (`pk_`, `fk_`, `uq_`, `ix_`, `ck_` prefixes)
- **Widget name max length:** 255, enforced in the service layer via `WidgetNameTooLongError` (not Pydantic `max_length`). Pydantic `WidgetCreate.name` is `str` without `max_length` — domain validation happens in the service so the error response carries rich context (`name`, `max_length`, `actual_length`). Pydantic catches structural issues (wrong type, missing required). The service catches domain issues.
- **Widget description max length:** none (Postgres `Text` type)

### 3.5 Testing Requirements

**Four test levels, all present, green, and exercising real code:**

| Level | Backend tooling | Frontend tooling | Location |
|---|---|---|---|
| Unit | pytest + pytest-asyncio | Vitest + React Testing Library | Backend: `tests/unit/`. Frontend: co-located `*.test.tsx` |
| Integration | pytest + pytest-asyncio + Testcontainers + httpx.AsyncClient | N/A (covered by E2E + unit) | `tests/integration/` |
| E2E | N/A (covered by Playwright) | Playwright | `tests/e2e/` |
| Contract | Schemathesis + generated client diff check | Generated client diff check | `tests/contract/` + CI |

**Type-driven discipline:**
- Pyright strict on backend, CI-enforced
- `tsc --noEmit` strict on frontend with `noUncheckedIndexedAccess` and `exactOptionalPropertyTypes`, CI-enforced

**Explicitly excluded:** property-based testing, performance testing, mutation testing, snapshot testing (forbidden), fuzz testing beyond Schemathesis.

### 3.6 Session Decisions

| Decision | Choice |
|---|---|
| Enforcement mechanism | `CLAUDE.md` only, no hooks |
| Backend architecture | Vertical slices |
| Pagination | Offset-based, `Page[T]` |
| Widget name max length | 255 |
| Frontend test location | Co-located |
| Frontend component files | `kebab-case.tsx`, each in own folder |
| Generated error code files | One file per class in `_generated/` directory |
| `ExternalApiClient` | Delete (dead code) |
| `debug.py` + debugpy | Delete |
| Branch strategy | `main` only, no `develop` |
| Storybook | Keep, co-located stories |
| Copilot review | Keep as second opinion |
| Terraform | Strip to empty shells, cloud-agnostic, add `staging` |
| `.env.example` | Repo root, template-used keys only |
| `scripts/` | Delete, Taskfile only |
| `Makefile` | Delete, Taskfile only |
| Dependabot | Rewrite paths, close stale PRs, rule in `CLAUDE.md` |
| Health endpoints | `/health` and `/ready` at root, outside `/api/v1/` |
| Validation errors | All field errors in `details` array |
| Money storage | SQLAlchemy `composite` mapping |
| `BaseRepository` | `BaseRepository[ModelT]` (one type param) |
| `BaseService` | `BaseService[ModelT, CreateT, ReadT, UpdateT]` (four type params) |

---

## 4. Paradigm Commitments

Every paradigm choice. One way to do each thing.

### 4.1 Error Handling

`DomainError` hierarchy generated from `packages/error-contracts/errors.yaml`. Each subclass maps to one HTTP status. One exception handler in `api/errors.py`. Route handlers raise `DomainError` subclasses, never `HTTPException`.

Error response shape:
```json
{
  "error": {
    "code": "WIDGET_NOT_FOUND",
    "params": {"widget_id": "abc-123"},
    "details": null,
    "request_id": "uuid-v7"
  }
}
```

`details` is always present. `null` for all errors except `VALIDATION_FAILED`, where it is an array of `{field, reason}` objects containing all validation errors.

Error codes are the i18n lookup keys. Frontend translates via `t(error.code, error.params)` against the `errors` namespace.

**Forbidden:** `HTTPException` anywhere except the handler file. `try/except` that swallows errors. Returning `None` for "not found."

### 4.2 Dependency Injection

FastAPI `Depends()` with factory functions. Repositories take `AsyncSession`. Services take their repository. Routers wire via `Depends`.

**Forbidden:** global singletons. Service locator. DI container libraries. Importing services directly.

### 4.3 Async Code

`async/await` everywhere. All route handlers, services, and repositories are `async def`. SQLAlchemy uses `AsyncSession`.

**Forbidden:** sync `def` route handlers. `run_in_executor`. Mixing sync/async paths.

### 4.4 Route Handler Shape

Thin. Declares dependencies, calls one service method, returns result. 2-3 lines of body.

**Forbidden:** business logic in handlers. `try/except` in handlers. Direct DB access. Multiple service calls.

### 4.5 Repository Pattern

`BaseRepository[ModelT]` — one type param. Knows only the SQLAlchemy model. Returns model instances. Raises `NotFoundError`. `list()` returns `tuple[list[ModelT], int]`.

All error classes (including `NotFoundError` and `ConflictError`) are generated from `errors.yaml`. `base.py` contains only the `DomainError` base class. The error hierarchy:

```
DomainError (hand-written, base.py)
├── NotFoundError        → 404, code="NOT_FOUND", no params (generated)
├── ConflictError        → 409, code="CONFLICT", no params (generated)
├── ValidationFailedError → 422, code="VALIDATION_FAILED" (generated)
├── InternalError        → 500, code="INTERNAL_ERROR" (generated)
├── RateLimitedError     → 429, code="RATE_LIMITED" (generated)
├── WidgetNotFoundError  → 404, code="WIDGET_NOT_FOUND", params: {widget_id} (generated)
├── WidgetNameConflictError → 409, code="WIDGET_NAME_CONFLICT", params: {name} (generated)
└── WidgetNameTooLongError → 422, code="WIDGET_NAME_TOO_LONG", params: {name, max_length, actual_length} (generated)
```

`BaseRepository` raises generic `NotFoundError` (no params) and `ConflictError` (no params). The **service** layer catches these and re-raises with entity-specific errors that carry typed params:

```python
# In WidgetService
async def get_by_id(self, entity_id: UUID) -> WidgetRead:
    try:
        model = await self.repository.get_by_id(entity_id)
    except NotFoundError:
        raise WidgetNotFoundError(widget_id=str(entity_id))
    return self._to_read(model)
```

This catch-and-re-raise is the canonical pattern for every entity. The repository stays generic; the service adds domain context. If a generic `NotFoundError` propagates without being caught (e.g., a developer forgets to override), the exception handler maps it to a generic 404 response with `NOT_FOUND` code — the frontend has a translation for it. But entity-specific codes are preferred and expected.

**Forbidden:** schema knowledge in repositories. Returning `None` for missing entities.

### 4.6 Service Pattern

`BaseService[ModelT, CreateT, ReadT, UpdateT]` — four type params. Wraps repository. Converts models → read schemas via `_to_read()`. Returns `ReadT` and `Page[ReadT]`.

**Forbidden:** business logic in repositories or handlers. Services importing from other features.

### 4.7 Pydantic Schemas

Hand-written, one class per file, three per entity: `Create`, `Read`, `Update`. Schemas never import models. Conversion in service layer.

**Forbidden:** schema factories. `model_validate` in handlers. Cross-entity schema inheritance.

### 4.8 Logging

structlog. Configured in `core/logging.py`. JSON in production, pretty in dev. Request ID processor.

**Forbidden:** `print`. `logging.getLogger`. f-string log messages.

### 4.9 Configuration

Single `Settings` class in `core/config.py`. pydantic-settings. Fail-fast on missing required values. Production rejects development defaults.

**Forbidden:** `os.getenv`. Inline defaults scattered in code. Separate config files per environment.

### 4.10 Frontend HTTP Client

`openapi-fetch` wrapping generated types. One wrapper at `shared/lib/api-client.ts`. All calls via TanStack Query hooks.

**Forbidden:** `fetch()` outside `api-client.ts`. Second HTTP library. API calls outside TanStack Query.

### 4.11 Frontend State Management

Server state: TanStack Query. Client state: Zustand (UI state only).

**Forbidden:** caching API data in Zustand. React Context for state. `useState` for server data.

### 4.12 Frontend Components

One component per file. File in its own folder with test and story. `kebab-case.tsx`, `PascalCase` export.

**Forbidden:** multiple components per file. Components without stories.

### 4.13 i18n

i18next + react-i18next. `/$lang/` URL prefix. Detection: localStorage `app.language` → `navigator.languages` → default `en`. Supported: `en`, `ro`. Error codes as i18n keys in `errors` namespace.

**Forbidden:** raw strings in JSX (enforced by ESLint i18next rule). String concatenation for translations. Manual date/number formatting (use `Intl` wrappers).

### 4.14 Storybook

Co-located `*.stories.tsx` next to every component. CSF3 format. Titles: `UI/*` for shadcn, `Features/<Feature>/*` for feature components.

**Forbidden:** stories in separate directory. Stories without the component they document.

### 4.15 Test Patterns

- **Backend unit:** pytest functions, no classes. Arrange-act-assert.
- **Backend integration:** Testcontainers Postgres, session-scoped container, transactional rollback per test, `httpx.AsyncClient`.
- **Frontend unit:** Vitest + RTL, `describe`/`it`, test behavior not implementation.
- **E2E:** Playwright, full stack via docker-compose, user-facing behavior descriptions.

**Forbidden:** test classes. `unittest.TestCase`. Tests with no assertions. Snapshot tests. SQLite as test DB. Mocking DB in integration tests.

---

## 5. Open Questions (Resolved)

All questions were resolved during the planning session. None remain open.

---

## 6. Target Architecture

### 6.1 Monorepo Root

```
fe-be-repo-model/
├── apps/
│   ├── backend/
│   └── frontend/
├── packages/
│   ├── api-client/
│   └── error-contracts/
├── infra/
│   ├── compose/
│   │   ├── docker-compose.yml
│   │   └── docker-compose.prod.yml
│   ├── docker/
│   │   ├── backend.Dockerfile
│   │   └── frontend.Dockerfile
│   └── terraform/
│       ├── modules/.gitkeep
│       └── environments/
│           ├── dev/backend.tf.example
│           ├── staging/backend.tf.example
│           └── prod/backend.tf.example
├── docs/
│   ├── architecture.md
│   ├── conventions.md
│   ├── decisions.md
│   ├── testing.md
│   └── runbook.md
├── .github/
│   ├── dependabot.yml
│   └── workflows/
│       ├── ci.yml
│       ├── copilot-review.yml
│       └── deploy.yml
├── .tool-versions
├── .editorconfig
├── .gitattributes
├── .gitignore
├── .pre-commit-config.yaml
├── .env.example
├── CLAUDE.md
├── CONTRIBUTING.md
├── README.md
├── LICENSE
├── TEMPLATE_FRICTION.md
├── Taskfile.yml
└── pnpm-workspace.yaml
```

### 6.2 Backend

```
apps/backend/
├── alembic/
│   ├── versions/
│   │   └── 0001_create_widget_table.py
│   ├── env.py
│   └── script.mako
├── alembic.ini
├── app/
│   ├── __init__.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── database.py
│   │   └── logging.py
│   ├── api/
│   │   ├── __init__.py
│   │   ├── errors.py
│   │   ├── middleware.py
│   │   ├── health_router.py
│   │   └── deps.py
│   ├── exceptions/
│   │   ├── __init__.py                 # Re-exports DomainError (from base) + all generated subclasses
│   │   ├── base.py                     # DomainError base class only
│   │   └── _generated/
│   │       ├── __init__.py             # Re-exports all generated classes
│   │       ├── _registry.py
│   │       ├── not_found_error.py      # Generic 404 — no params. Used by BaseRepository.
│   │       ├── conflict_error.py       # Generic 409 — no params. Used by BaseRepository.
│   │       ├── validation_failed_error.py
│   │       ├── validation_failed_params.py
│   │       ├── internal_error.py
│   │       ├── rate_limited_error.py
│   │       ├── rate_limited_params.py
│   │       ├── widget_not_found_error.py
│   │       ├── widget_not_found_params.py
│   │       ├── widget_name_conflict_error.py
│   │       ├── widget_name_conflict_params.py
│   │       ├── widget_name_too_long_error.py
│   │       └── widget_name_too_long_params.py
│   ├── shared/
│   │   ├── __init__.py
│   │   ├── base_model.py
│   │   ├── base_repository.py
│   │   └── base_service.py
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── page.py
│   │   └── error_response.py          # OpenAPI documentation schema only (see note below)
│   ├── types/
│   │   ├── __init__.py
│   │   ├── money.py
│   │   └── currency.py
│   ├── features/
│   │   ├── __init__.py
│   │   └── widget/
│   │       ├── __init__.py
│   │       ├── model.py
│   │       ├── repository.py
│   │       ├── service.py
│   │       ├── router.py
│   │       └── schemas/
│   │           ├── __init__.py
│   │           ├── widget_create.py
│   │           ├── widget_read.py
│   │           └── widget_update.py
│   └── main.py
├── tests/
│   ├── __init__.py
│   ├── unit/
│   │   ├── __init__.py
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   └── test_config.py
│   │   ├── features/
│   │   │   ├── __init__.py
│   │   │   └── widget/
│   │   │       ├── __init__.py
│   │   │       └── test_widget_service.py
│   │   ├── exceptions/
│   │   │   ├── __init__.py
│   │   │   ├── test_error_handler.py
│   │   │   └── test_domain_errors.py
│   │   ├── types/
│   │   │   ├── __init__.py
│   │   │   └── test_money.py
│   │   └── schemas/
│   │       ├── __init__.py
│   │       └── test_page.py
│   ├── integration/
│   │   ├── __init__.py
│   │   ├── features/
│   │   │   ├── __init__.py
│   │   │   └── widget/
│   │   │       ├── __init__.py
│   │   │       └── test_widget_endpoints.py
│   │   ├── shared/
│   │   │   ├── __init__.py
│   │   │   ├── test_base_repository.py
│   │   │   └── test_base_service.py
│   │   ├── test_health.py
│   │   ├── test_rollback_canary.py
│   │   └── conftest.py
│   └── contract/
│       ├── __init__.py
│       └── test_schemathesis.py
├── architecture/
│   └── import-linter-contracts.ini
├── pyproject.toml
└── .dockerignore
```

**Note on `error_response.py`:** The `ErrorResponse` Pydantic model exists solely for OpenAPI documentation — it is used as `responses={...}` metadata on route decorators so the generated TypeScript client and Swagger UI know the error shape. The exception handler in `api/errors.py` builds error responses directly as `JSONResponse` dicts at runtime. `ErrorResponse` is never instantiated in application code.

### 6.3 Frontend

```
apps/frontend/
├── .storybook/
│   ├── main.ts
│   └── preview.ts
├── src/
│   ├── routes/
│   │   ├── __root.tsx
│   │   ├── index.tsx
│   │   └── $lang/
│   │       ├── route.tsx
│   │       └── widgets/
│   │           └── index.tsx
│   ├── features/
│   │   └── widgets/
│   │       ├── api/
│   │       │   ├── use-widgets.ts
│   │       │   ├── use-widget.ts
│   │       │   ├── use-create-widget.ts
│   │       │   ├── use-update-widget.ts
│   │       │   └── use-delete-widget.ts
│   │       └── components/
│   │           ├── widget-list/
│   │           │   ├── widget-list.tsx
│   │           │   ├── widget-list.test.tsx
│   │           │   └── widget-list.stories.tsx
│   │           └── widget-form/
│   │               ├── widget-form.tsx
│   │               ├── widget-form.test.tsx
│   │               └── widget-form.stories.tsx
│   ├── shared/
│   │   ├── components/
│   │   │   ├── ui/
│   │   │   │   ├── button.tsx
│   │   │   │   ├── button.stories.tsx
│   │   │   │   ├── input.tsx
│   │   │   │   ├── input.stories.tsx
│   │   │   │   └── ...
│   │   │   ├── error-message/
│   │   │   │   ├── error-message.tsx
│   │   │   │   ├── error-message.test.tsx
│   │   │   │   └── error-message.stories.tsx
│   │   │   ├── error-display/
│   │   │   │   ├── error-display.tsx
│   │   │   │   ├── error-display.test.tsx
│   │   │   │   └── error-display.stories.tsx
│   │   │   ├── date-time/
│   │   │   │   ├── date-time.tsx
│   │   │   │   ├── date-time.test.tsx
│   │   │   │   └── date-time.stories.tsx
│   │   │   ├── money-display/
│   │   │   │   ├── money-display.tsx
│   │   │   │   ├── money-display.test.tsx
│   │   │   │   └── money-display.stories.tsx
│   │   │   └── language-switcher/
│   │   │       ├── language-switcher.tsx
│   │   │       ├── language-switcher.test.tsx
│   │   │       └── language-switcher.stories.tsx
│   │   ├── hooks/
│   │   │   ├── use-current-language.ts
│   │   │   └── use-pagination.ts
│   │   ├── lib/
│   │   │   ├── api-client.ts
│   │   │   ├── api-client.test.ts
│   │   │   ├── money.ts
│   │   │   ├── money.test.ts
│   │   │   ├── format.ts
│   │   │   ├── logger.ts
│   │   │   └── cn.ts
│   │   └── types/
│   │       └── api-error.ts
│   ├── i18n/
│   │   ├── config.ts
│   │   └── locales/
│   │       ├── en/
│   │       │   ├── common.json
│   │       │   └── errors.json
│   │       └── ro/
│   │           ├── common.json
│   │           └── errors.json
│   ├── stores/
│   ├── app/
│   │   ├── error-boundary.tsx
│   │   └── providers.tsx
│   └── main.tsx
├── tests/
│   └── e2e/
│       ├── widget-crud.spec.ts
│       └── fixtures/
├── index.html
├── vite.config.ts
├── vitest.config.ts
├── tsconfig.json
├── components.json
├── tailwind.config.ts
├── biome.json
├── .eslintrc.cjs
└── package.json
```

### 6.4 Packages

```
packages/
├── api-client/
│   ├── src/
│   │   └── schema.d.ts
│   ├── package.json
│   └── tsconfig.json
└── error-contracts/
    ├── errors.yaml
    ├── scripts/
    │   ├── generate.py
    │   └── validate-translations.py
    ├── src/
    │   ├── index.ts
    │   ├── generated.ts
    │   └── required-keys.json
    ├── tests/
    │   ├── test_generate.py
    │   └── test_validate_translations.py
    ├── package.json
    └── pyproject.toml
```

### 6.5 Taskfile Command Surface

```yaml
task dev              # Start full stack via docker-compose
task dev:backend      # Start backend only with hot reload
task dev:frontend     # Start frontend only with Vite HMR

task check            # ALL checks (Claude runs this before declaring done)
task check:lint       # ruff + biome + eslint (i18next rule)
task check:types      # pyright strict + tsc --noEmit
task check:arch       # import-linter
task check:test       # pytest unit + vitest
task check:errors     # errors:generate --check + errors:check

task test             # All tests including integration
task test:unit        # pytest unit + vitest
task test:integration # pytest integration (Testcontainers)
task test:e2e         # Playwright against docker-compose
task test:contract    # Schemathesis + client diff check

task lint             # ruff + biome + eslint
task format           # ruff format + biome format

task db:migrate       # alembic upgrade head
task db:revision      # alembic revision --autogenerate
task db:reset         # drop + recreate + migrate (dev only)

task client:generate  # openapi-typescript → packages/api-client/
task errors:generate  # errors.yaml → Python + TypeScript + JSON
task errors:check     # Validate translations against contracts

task storybook        # Start Storybook dev server
task storybook:build  # Build Storybook (CI check)

task docker:build     # Build all Docker images
task docker:up        # docker-compose up
task docker:down      # docker-compose down
```

### 6.6 import-linter Contracts

```ini
[importlinter]
root_package = app

[importlinter:contract:feature-isolation]
name = Features must not import from other features
type = independence
modules =
    app.features.widget

[importlinter:contract:feature-layering]
name = Feature internal layering: router → service → repository
type = layers
layers =
    app.features.{}.router
    app.features.{}.service
    app.features.{}.repository
containers =
    app.features

[importlinter:contract:schemas-no-models]
name = Schemas must not import SQLAlchemy models
type = forbidden
source_modules =
    app.features.widget.schemas
    app.schemas
forbidden_modules =
    app.features.widget.model
    app.shared.base_model

[importlinter:contract:generated-exceptions-isolation]
name = Generated exceptions only imported by exceptions __init__
type = forbidden
source_modules =
    app.features
    app.api
    app.shared
    app.core
    app.schemas
    app.types
forbidden_modules =
    app.exceptions._generated

[importlinter:contract:shared-no-features]
name = Shared and core must not import from features
type = forbidden
source_modules =
    app.shared
    app.core
    app.schemas
    app.types
forbidden_modules =
    app.features
```

---

## 7. Phase-by-Phase Plan

### Phase 1: Foundation Files + Monorepo Restructure

**Goal:** Config files, workspace setup, tooling, discipline documents, and structural reorganization into `apps/`/`packages/`/`infra/` layout. No application code changes.

**Structural moves (first step of this phase):**
- Move `backend/` → `apps/backend/` (includes `backend/architecture/import-linter-contracts.ini` → `apps/backend/architecture/import-linter-contracts.ini`)
- Create `apps/frontend/` (empty — populated in Phase 6)
- Create `packages/` (populated in Phase 3)
- Create `infra/` (populated in Phase 4 and Phase 9)
- Note: `frontend/` stays at its current location until Phase 5 deletes it. It is not moved to `apps/` because it is deleted entirely. All Phase 5 deletion paths reference `frontend/` (current location).

**Files created:**
- `CLAUDE.md`, `.tool-versions`, `.editorconfig`, `.gitattributes`
- `Taskfile.yml`, `pnpm-workspace.yaml`, `.env.example`
- `docs/decisions.md`, `docs/conventions.md`, `docs/architecture.md`, `docs/testing.md`

**Files deleted:**
- `Makefile`, `scripts/` (entire directory), root `package-lock.json`, `env-files/`

**Files modified:**
- `.gitignore`, `.pre-commit-config.yaml`, `.vscode/settings.json`, `.vscode/extensions.json`
- All internal imports in `apps/backend/` updated if the move changes Python package resolution (it should not — `app/` remains the Python package root, only the containing directory changes)

**Pre-commit / pre-push hook strategy:**
- **Pre-commit** (via `pre-commit` framework, `.pre-commit-config.yaml`): ruff, biome, trailing-whitespace, end-of-file-fixer, check-yaml, check-json, check-added-large-files. Fast checks only (~10-15s).
- **Pre-push** (via `pre-commit` framework with `stages: [pre-push]`): `pytest tests/unit/` and `vitest run`. Runs unit tests before code leaves the machine. Configured in `.pre-commit-config.yaml` using `local` hooks with `stages: [pre-push]`.
- **CI:** everything — all four test levels, all linters, all type checkers, Storybook build, generated file diff checks.
- **`CLAUDE.md` rule:** Claude runs `task check` (which covers all pre-commit + pre-push + CI checks) before declaring any work done.

**Entry criteria:** Branch created from `main`.

**Exit criteria:** `CLAUDE.md` complete. `.tool-versions` pins versions. `Taskfile.yml` has stub commands. All docs have real content. `apps/backend/` exists with the moved backend code. Existing backend tests still pass from the new location.

---

### Phase 2: Backend Core

**Goal:** Database connection, configuration, structured logging, middleware, health/readiness endpoints, money types.

**TDD sequence:**
1. Test config validation (2 tests: `test_settings_validates_required_database_url`, `test_settings_accepts_valid_config`) → implement `core/config.py`
2. Test money type (4 tests: `test_money_creation_with_valid_currency`, `test_money_rejects_invalid_currency_code`, `test_money_amount_is_integer_minor_units`, `test_money_rejects_float_amount`) → implement `types/money.py` + `types/currency.py`
3. Testcontainers `conftest.py` + rollback canary test (1 test: `test_transaction_rollback_undoes_insert`) → implement `core/database.py`
4. Test health/readiness/middleware (7 tests: `test_health_returns_200`, `test_ready_returns_200_when_db_reachable`, `test_ready_returns_503_when_db_unreachable`, `test_response_includes_x_request_id`, `test_response_includes_security_headers`, `test_cors_allows_configured_origin`, `test_cors_rejects_unconfigured_origin`) → implement `api/health_router.py`, `api/middleware.py`
5. Implement `core/logging.py`, `shared/base_model.py`, `api/deps.py`
6. Initialize Alembic

**Files deleted:**
- `apps/backend/app/modules/`, `apps/backend/app/infrastructure/`, `apps/backend/app/domain/`, `apps/backend/app/services/`
- All old tests (`apps/backend/tests/test_*.py`), `apps/backend/debug.py`

**Exit criteria:** Health + readiness work. Middleware sets request ID and security headers. CORS configured. Testcontainers Postgres works. Rollback canary passes. Pyright strict passes. Ruff passes.

---

### Phase 3: Error Contracts System

**Goal:** `errors.yaml`, codegen, generated error classes (one per file), exception handler, translation validator.

**TDD sequence:**
1. Test codegen (6 tests) → implement `generate.py`
2. Run codegen → produces `_generated/` directory with one file per class
3. Test translation validator (6 tests) → implement `validate-translations.py`
4. Create `apps/frontend/src/i18n/locales/en/errors.json` and `apps/frontend/src/i18n/locales/ro/errors.json` with all error translations. Note: this creates the `i18n/locales/` directory structure before the React app exists (Phase 6). These are data files, not app code — they can exist independently. The rest of `apps/frontend/` is populated in Phase 6.
5. Run `task errors:check` → validator passes
6. Create `app/schemas/error_response.py` — Pydantic model for OpenAPI documentation of the error response shape (not instantiated at runtime; used as `responses={}` metadata on route decorators)
7. Test generated domain error classes (1 test: `test_domain_error_constructs_with_typed_params`) → verify generated classes work
8. Test exception handler (4 tests: `test_error_handler_serializes_domain_error`, `test_error_handler_maps_validation_error`, `test_error_handler_includes_all_validation_errors_in_details`, `test_error_handler_maps_unhandled_to_internal_error`) → implement `api/errors.py`
9. Wire into `main.py`

**Exit criteria:** `task errors:generate` produces all files. `task errors:check` passes (translation files exist and are valid). Exception handler returns typed error responses. `ErrorResponse` schema registered in OpenAPI metadata.

---

### Phase 4: Generic CRUD + Widget + Docker Compose

**Goal:** `BaseRepository`, `BaseService`, Widget feature with full CRUD, Alembic migration, docker-compose for dev stack.

**TDD sequence:**
1. Test `Page[T]` → implement `schemas/page.py`
2. Test `BaseRepository` via DummyModel (9 integration tests) → implement `base_repository.py`
3. Test `BaseService` via DummyService (unit + integration) → implement `base_service.py`
4. Test `WidgetService` (5 unit tests) → implement schemas + service
5. Test Widget endpoints (14 integration tests) → implement model, repository, router
6. Generate Alembic migration
7. Create `infra/compose/docker-compose.yml` (backend + postgres:17)

**Exit criteria:** Full Widget CRUD works. All tests green. Alembic migration runs. Docker-compose starts backend + postgres. Pyright strict passes. import-linter passes.

---

### Phase 5: Angular Deletion

**Goal:** Remove everything Angular.

**Files deleted:** `frontend/` (entire directory), root `docker-compose.dev.yml`, root `docker-compose.prod.yml`

**Verification:** `git grep -i angular` returns zero. Backend tests still green.

**Exit criteria:** No Angular artifacts in tree. CI backend job passes.

---

### Phase 6: Frontend Foundation

**Goal:** React + Vite + TypeScript frontend with i18n, routing, Storybook, shared components, error handling.

**TDD sequence:**
1. Scaffold Vite + React + TypeScript
2. Test api-client (4 tests) → implement `api-client.ts`
3. Test money wrapper (2 tests) → implement `money.ts`
4. Test each shared component (test → implement → story):
   - `error-message/` (3 tests: renders localized EN, renders localized RO, renders request ID)
   - `error-display/` (1 test: renders error with request ID)
   - `date-time/` (2 tests: renders in user locale, renders in Romanian)
   - `money-display/` (2 tests: renders currency, renders RO locale)
   - `language-switcher/` (2 tests: lists languages, persists to localStorage)
5. Wire routing: `__root.tsx` → `index.tsx` → `$lang/route.tsx`
6. Wire providers, error boundary
7. Configure Storybook, scaffold shadcn/ui
8. Add frontend service to `infra/compose/docker-compose.yml`

**Exit criteria:** Vite dev server works. i18n detection + switching works. All shared component tests pass. All stories render. Storybook builds. tsc strict passes. Biome + ESLint i18next passes.

---

### Phase 7: Frontend Widget Feature + E2E

**Goal:** Widgets page, components, TanStack Query hooks, Playwright E2E.

**TDD sequence:**
1. Test `WidgetList` (4 states) → implement → story
2. Test `WidgetForm` (3 behaviors) → implement → story
3. Implement API hooks
4. Wire route page
5. Playwright E2E: create widget, see in list

**Exit criteria:** Widgets page works at `/en/widgets` and `/ro/widgets`. All component tests pass. E2E passes against full stack.

---

### Phase 8: Packages Integration + Contract Tests

**Goal:** API client generation, Schemathesis, generated client diff check.

**TDD sequence:**
1. Configure Schemathesis → verify passes
2. Run `task client:generate` → commit `schema.d.ts`
3. Wire CI: client diff check, error codegen diff check

**Exit criteria:** All four test levels green. CI catches stale generated files. Frontend types match backend spec.

---

### Phase 9: Infra and Docs Finalization

**Goal:** Dockerfiles, Terraform, CI workflows, documentation, Dependabot.

**Files created:**
- `infra/docker/backend.Dockerfile`, `infra/docker/frontend.Dockerfile`
- `infra/compose/docker-compose.prod.yml`
- `infra/terraform/environments/{dev,staging,prod}/backend.tf.example`
- `docs/runbook.md`, `TEMPLATE_FRICTION.md`

**Files modified:**
- `infra/terraform/main.tf` (strip AWS, make cloud-agnostic)
- `.github/workflows/ci.yml` (final version)
- `.github/workflows/deploy.yml`
- `.github/dependabot.yml`
- `CONTRIBUTING.md`, `README.md`

**Files deleted:**
- `infrastructure/` (old Terraform location)
- All stale docs

**Exit criteria:** Docker builds work. Terraform is empty shells. CI runs all checks. All docs match reality.

---

### Phase 10: Final Verification

**Goal:** Prove everything works. Sweep for forbidden patterns. Fix anything found.

**Verification:**
```bash
task check                              # All checks
task test                               # All tests
task test:e2e                           # Full stack E2E
task storybook:build                    # Stories build
git grep -i angular                     # Zero
git grep "HTTPException"                # Zero outside api/errors.py
git grep "print("                       # Zero in app/
git grep "console.log"                  # Zero in src/
git grep "datetime.now()"              # Zero (use datetime.now(UTC))
git grep "datetime.utcnow"            # Zero
git grep "float.*price\|float.*amount" # Zero
git grep "TIMESTAMP[^T]"              # Zero (use TIMESTAMPTZ)
git grep "logging.getLogger"           # Zero
git grep "os.environ"                   # Zero in app/
git grep "fetch("                       # Zero outside api-client.ts
```

**Exit criteria:** Every check passes. PR ready for review.

---

## 8. Generic CRUD Abstraction Design

### 8.1 `BaseRepository[ModelT]`

File: `apps/backend/app/shared/base_repository.py`

```python
class BaseRepository(Generic[ModelT]):
    def __init__(self, session: AsyncSession, model_class: type[ModelT]) -> None:
        self.session = session
        self.model_class = model_class

    async def create(self, **kwargs: object) -> ModelT: ...
    async def get_by_id(self, entity_id: UUID) -> ModelT: ...  # raises NotFoundError
    async def list(self, *, page: int = 1, size: int = 20) -> tuple[list[ModelT], int]: ...
    async def update(self, entity_id: UUID, **kwargs: object) -> ModelT: ...  # raises NotFoundError
    async def delete(self, entity_id: UUID) -> None: ...  # raises NotFoundError
```

- `model_class` passed in constructor (Python generics erased at runtime)
- `create()`/`update()` take `**kwargs` — service unpacks schemas
- `list()` returns `tuple[list[ModelT], int]` — service constructs `Page[ReadT]`
- `get_by_id()` raises `NotFoundError`, never returns `None`

### 8.2 `BaseService[ModelT, CreateT, ReadT, UpdateT]`

File: `apps/backend/app/shared/base_service.py`

```python
class BaseService(Generic[ModelT, CreateT, ReadT, UpdateT]):
    def __init__(self, repository: BaseRepository[ModelT]) -> None:
        self.repository = repository

    def _to_read(self, model: ModelT) -> ReadT:
        raise NotImplementedError  # subclass MUST override

    async def create(self, data: CreateT) -> ReadT: ...
    async def get_by_id(self, entity_id: UUID) -> ReadT: ...
    async def list(self, *, page: int = 1, size: int = 20) -> Page[ReadT]: ...
    async def update(self, entity_id: UUID, data: UpdateT) -> ReadT: ...
    async def delete(self, entity_id: UUID) -> None: ...
```

- `_to_read()` is the single model → schema conversion point
- `update()` uses `model_dump(exclude_unset=True)` for PATCH semantics
- `list()` constructs `Page[ReadT]` from repository's tuple

### 8.3 Widget Router

```python
router = APIRouter(prefix="/widgets", tags=["widgets"])

@router.post("", status_code=201, response_model=WidgetRead)
async def create_widget(data: WidgetCreate, service: WidgetService = Depends(get_widget_service)) -> WidgetRead:
    return await service.create(data)

@router.get("", response_model=Page[WidgetRead])
async def list_widgets(page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100), service: WidgetService = Depends(get_widget_service)) -> Page[WidgetRead]:
    return await service.list(page=page, size=size)

@router.get("/{widget_id}", response_model=WidgetRead)
async def get_widget(widget_id: UUID, service: WidgetService = Depends(get_widget_service)) -> WidgetRead:
    return await service.get_by_id(widget_id)

@router.patch("/{widget_id}", response_model=WidgetRead)
async def update_widget(widget_id: UUID, data: WidgetUpdate, service: WidgetService = Depends(get_widget_service)) -> WidgetRead:
    return await service.update(widget_id, data)

@router.delete("/{widget_id}", status_code=204)
async def delete_widget(widget_id: UUID, service: WidgetService = Depends(get_widget_service)) -> None:
    await service.delete(widget_id)
```

Mounted in `main.py` under `APIRouter(prefix="/api/v1")`. Health/readiness mounted at root.

### 8.4 Adding a New Entity

1. `features/<entity>/model.py` — SQLAlchemy model
2. `features/<entity>/schemas/` — `_create.py`, `_read.py`, `_update.py`
3. `features/<entity>/repository.py` — `FooRepository(BaseRepository[Foo])`
4. `features/<entity>/service.py` — `FooService(BaseService[Foo, ...])`
5. `features/<entity>/router.py` — concrete router
6. Add model import to `alembic/env.py`
7. Run `alembic revision --autogenerate`
8. Add error codes to `errors.yaml`, run `task errors:generate`
9. Add translations
10. Add to import-linter independence contract
11. Tests at every level

---

## 9. Frontend Widgets Feature Design

### 9.1 Components

**`WidgetList`** — fetches via `useWidgets()`, renders: loading, empty, populated, error states. Error state uses `<ErrorMessage>`. Timestamps via `<DateTime>`.

**`WidgetForm`** — controlled form, name (required) + description (optional). Submit via `useCreateWidget()`. On success: clear form, invalidate list. On error: inline `<ErrorMessage>`. Disabled while pending.

**Widgets route** — composes `<WidgetForm>` + `<WidgetList>`.

### 9.2 E2E

One test: `user can create a widget and see it in the list`. Opens `/en/widgets`, fills form, submits, asserts widget appears.

### 9.3 Stories

`widget-list.stories.tsx`: Empty, WithWidgets, Loading, Error
`widget-form.stories.tsx`: Default, Submitting, WithError

---

## 10. Test Matrix

### Backend Unit Tests

| Test Name | File | Phase |
|---|---|---|
| `test_settings_validates_required_database_url` | `tests/unit/core/test_config.py` | 2 |
| `test_settings_accepts_valid_config` | `tests/unit/core/test_config.py` | 2 |
| `test_money_creation_with_valid_currency` | `tests/unit/types/test_money.py` | 2 |
| `test_money_rejects_invalid_currency_code` | `tests/unit/types/test_money.py` | 2 |
| `test_money_amount_is_integer_minor_units` | `tests/unit/types/test_money.py` | 2 |
| `test_money_rejects_float_amount` | `tests/unit/types/test_money.py` | 2 |
| `test_domain_error_constructs_with_typed_params` | `tests/unit/exceptions/test_domain_errors.py` | 3 |
| `test_error_handler_serializes_domain_error` | `tests/unit/exceptions/test_error_handler.py` | 3 |
| `test_error_handler_maps_validation_error` | `tests/unit/exceptions/test_error_handler.py` | 3 |
| `test_error_handler_includes_all_validation_errors_in_details` | `tests/unit/exceptions/test_error_handler.py` | 3 |
| `test_error_handler_maps_unhandled_to_internal_error` | `tests/unit/exceptions/test_error_handler.py` | 3 |
| `test_page_calculates_pages_correctly` | `tests/unit/schemas/test_page.py` | 4 |
| `test_page_zero_total_returns_zero_pages` | `tests/unit/schemas/test_page.py` | 4 |
| `test_widget_service_create_returns_widget_read` | `tests/unit/features/widget/test_widget_service.py` | 4 |
| `test_widget_service_get_by_id_returns_widget_read` | `tests/unit/features/widget/test_widget_service.py` | 4 |
| `test_widget_service_get_by_id_raises_not_found` | `tests/unit/features/widget/test_widget_service.py` | 4 |
| `test_widget_service_update_returns_updated_read` | `tests/unit/features/widget/test_widget_service.py` | 4 |
| `test_widget_service_delete_delegates_to_repository` | `tests/unit/features/widget/test_widget_service.py` | 4 |

### Backend Integration Tests

| Test Name | File | Phase |
|---|---|---|
| `test_health_returns_200` | `tests/integration/test_health.py` | 2 |
| `test_ready_returns_200_when_db_reachable` | `tests/integration/test_health.py` | 2 |
| `test_ready_returns_503_when_db_unreachable` | `tests/integration/test_health.py` | 2 |
| `test_response_includes_x_request_id` | `tests/integration/test_health.py` | 2 |
| `test_response_includes_security_headers` | `tests/integration/test_health.py` | 2 |
| `test_cors_allows_configured_origin` | `tests/integration/test_health.py` | 2 |
| `test_cors_rejects_unconfigured_origin` | `tests/integration/test_health.py` | 2 |
| `test_transaction_rollback_undoes_insert` | `tests/integration/test_rollback_canary.py` | 2 |
| `test_base_repository_create_persists` | `tests/integration/shared/test_base_repository.py` | 4 |
| `test_base_repository_get_by_id_returns` | `tests/integration/shared/test_base_repository.py` | 4 |
| `test_base_repository_get_by_id_raises_not_found` | `tests/integration/shared/test_base_repository.py` | 4 |
| `test_base_repository_list_returns_items_and_total_count` | `tests/integration/shared/test_base_repository.py` | 4 |
| `test_base_repository_list_returns_empty_when_no_data` | `tests/integration/shared/test_base_repository.py` | 4 |
| `test_base_repository_update_modifies` | `tests/integration/shared/test_base_repository.py` | 4 |
| `test_base_repository_update_raises_not_found` | `tests/integration/shared/test_base_repository.py` | 4 |
| `test_base_repository_delete_removes` | `tests/integration/shared/test_base_repository.py` | 4 |
| `test_base_repository_delete_raises_not_found` | `tests/integration/shared/test_base_repository.py` | 4 |
| `test_base_service_create_persists_and_returns` | `tests/integration/shared/test_base_service.py` | 4 |
| `test_base_service_full_crud_lifecycle` | `tests/integration/shared/test_base_service.py` | 4 |
| `test_create_widget_persists_and_returns` | `tests/integration/features/widget/test_widget_endpoints.py` | 4 |
| `test_get_widget_returns_when_exists` | `tests/integration/features/widget/test_widget_endpoints.py` | 4 |
| `test_get_widget_returns_404_with_code_and_params` | `tests/integration/features/widget/test_widget_endpoints.py` | 4 |
| `test_list_widgets_returns_paginated` | `tests/integration/features/widget/test_widget_endpoints.py` | 4 |
| `test_list_widgets_returns_empty_page` | `tests/integration/features/widget/test_widget_endpoints.py` | 4 |
| `test_patch_widget_updates_and_bumps_updated_at` | `tests/integration/features/widget/test_widget_endpoints.py` | 4 |
| `test_patch_widget_returns_404_when_missing` | `tests/integration/features/widget/test_widget_endpoints.py` | 4 |
| `test_delete_widget_removes` | `tests/integration/features/widget/test_widget_endpoints.py` | 4 |
| `test_delete_widget_returns_404_when_missing` | `tests/integration/features/widget/test_widget_endpoints.py` | 4 |
| `test_create_widget_duplicate_name_returns_409` | `tests/integration/features/widget/test_widget_endpoints.py` | 4 |
| `test_create_widget_empty_name_returns_422` | `tests/integration/features/widget/test_widget_endpoints.py` | 4 |
| `test_create_widget_name_too_long_returns_422_with_widget_name_too_long_code` | `tests/integration/features/widget/test_widget_endpoints.py` | 4 |
| `test_all_responses_include_request_id` | `tests/integration/features/widget/test_widget_endpoints.py` | 4 |
| `test_error_response_includes_request_id_in_body` | `tests/integration/features/widget/test_widget_endpoints.py` | 4 |

### Backend Contract Tests

| Test Name | File | Phase |
|---|---|---|
| `test_schemathesis_all_widget_endpoints` | `tests/contract/test_schemathesis.py` | 8 |
| CI: `task client:generate` + `git diff --exit-code` | CI workflow | 8 |
| CI: `task errors:generate --check` | CI workflow | 8 |

### Codegen + Validator Tests

| Test Name | File | Phase |
|---|---|---|
| `test_codegen_produces_valid_python` | `packages/error-contracts/tests/test_generate.py` | 3 |
| `test_codegen_produces_valid_typescript` | `packages/error-contracts/tests/test_generate.py` | 3 |
| `test_codegen_produces_valid_required_keys` | `packages/error-contracts/tests/test_generate.py` | 3 |
| `test_codegen_rejects_duplicate_codes` | `packages/error-contracts/tests/test_generate.py` | 3 |
| `test_codegen_rejects_invalid_http_status` | `packages/error-contracts/tests/test_generate.py` | 3 |
| `test_codegen_rejects_invalid_param_type` | `packages/error-contracts/tests/test_generate.py` | 3 |
| `test_validator_passes_complete` | `packages/error-contracts/tests/test_validate_translations.py` | 3 |
| `test_validator_fails_missing_key` | `packages/error-contracts/tests/test_validate_translations.py` | 3 |
| `test_validator_fails_extra_key` | `packages/error-contracts/tests/test_validate_translations.py` | 3 |
| `test_validator_fails_undefined_param` | `packages/error-contracts/tests/test_validate_translations.py` | 3 |
| `test_validator_fails_missing_param` | `packages/error-contracts/tests/test_validate_translations.py` | 3 |
| `test_validator_fails_empty_translation` | `packages/error-contracts/tests/test_validate_translations.py` | 3 |

### Frontend Unit Tests

| Test Name | File | Phase |
|---|---|---|
| `ApiError constructs from payload` | `shared/lib/api-client.test.ts` | 6 |
| `ApiError is() narrows type` | `shared/lib/api-client.test.ts` | 6 |
| `api-client throws ApiError on non-2xx` | `shared/lib/api-client.test.ts` | 6 |
| `api-client includes request ID` | `shared/lib/api-client.test.ts` | 6 |
| `money creates from minor units` | `shared/lib/money.test.ts` | 6 |
| `money rejects mixed currency` | `shared/lib/money.test.ts` | 6 |
| `ErrorMessage renders localized text EN` | `shared/components/error-message/error-message.test.tsx` | 6 |
| `ErrorMessage renders localized text RO` | `shared/components/error-message/error-message.test.tsx` | 6 |
| `ErrorMessage renders request ID` | `shared/components/error-message/error-message.test.tsx` | 6 |
| `ErrorDisplay renders error with request ID` | `shared/components/error-display/error-display.test.tsx` | 6 |
| `DateTime renders in user locale` | `shared/components/date-time/date-time.test.tsx` | 6 |
| `DateTime renders in Romanian` | `shared/components/date-time/date-time.test.tsx` | 6 |
| `MoneyDisplay renders currency` | `shared/components/money-display/money-display.test.tsx` | 6 |
| `MoneyDisplay renders RO locale` | `shared/components/money-display/money-display.test.tsx` | 6 |
| `LanguageSwitcher lists languages` | `shared/components/language-switcher/language-switcher.test.tsx` | 6 |
| `LanguageSwitcher persists to localStorage` | `shared/components/language-switcher/language-switcher.test.tsx` | 6 |
| `WidgetList renders empty state` | `features/widgets/components/widget-list/widget-list.test.tsx` | 7 |
| `WidgetList renders list` | `features/widgets/components/widget-list/widget-list.test.tsx` | 7 |
| `WidgetList renders loading` | `features/widgets/components/widget-list/widget-list.test.tsx` | 7 |
| `WidgetList renders error` | `features/widgets/components/widget-list/widget-list.test.tsx` | 7 |
| `WidgetForm submits valid input` | `features/widgets/components/widget-form/widget-form.test.tsx` | 7 |
| `WidgetForm shows error on empty name` | `features/widgets/components/widget-form/widget-form.test.tsx` | 7 |
| `WidgetForm disables submit while pending` | `features/widgets/components/widget-form/widget-form.test.tsx` | 7 |

### Frontend E2E

| Test Name | File | Phase |
|---|---|---|
| `user can create a widget and see it in the list` | `tests/e2e/widget-crud.spec.ts` | 7 |

### Enforcement Checklist

1. ☑ Unit tests for every Widget service method
2. ☑ Repository logic tested at integration level against real Postgres
3. ☑ Integration tests for every Widget CRUD endpoint
4. ☑ BaseRepository tested independently via DummyModel
5. ☑ BaseService tested independently via DummyService
6. ☑ One Playwright E2E test: create + see in list
7. ☑ Schemathesis configured, runs in CI, covers all Widget endpoints
8. ☑ `task client:generate` in CI, fails on stale diff
9. ☑ Error handling path tested per status code + integration
10. ☑ Pyright strict in CI
11. ☑ tsc --noEmit strict in CI
12. ☑ `task check` runs all checks

---

## 11. Angular Removal Checklist

**File-level:**
- [ ] `frontend/` directory deleted
- [ ] Root `docker-compose.dev.yml` deleted
- [ ] Root `docker-compose.prod.yml` deleted
- [ ] Root `package-lock.json` deleted

**Grep-level (all return zero):**
- [ ] `git grep -i angular`
- [ ] `git grep -i karma`
- [ ] `git grep -i jasmine`
- [ ] `git grep "zone.js"`
- [ ] `git grep "storybook-static"`
- [ ] `git grep "ng serve"`
- [ ] `git grep "ng lint"`
- [ ] `git grep "ng test"`
- [ ] `git grep "@angular"`
- [ ] `git grep "\.scss"`
- [ ] `git grep "proxy\.conf"`
- [ ] `git grep "4200"`

**CI-level:**
- [ ] `frontend-tests` job removed temporarily
- [ ] Frontend Docker build removed temporarily
- [ ] `.pre-commit-config.yaml` has no `prettier`
- [ ] `.vscode/extensions.json` has no `angular.ng-template`

---

## 12. CLAUDE.md Draft

```markdown
# CLAUDE.md

This file is the discipline contract for AI-assisted development on this repository.
Every rule is mandatory. "Forbidden" means "do not do this under any circumstances
without stopping and asking the user first." Violations are bugs.

## Project Overview

Full-stack monorepo: FastAPI backend, React frontend, Postgres database.
Template repository — decisions here compound across every project built from it.

## Stack (do not deviate)

- Python 3.13, Node 22 LTS, pnpm 10, uv
- Backend: FastAPI, Pydantic v2, pydantic-settings, SQLAlchemy 2.0 async, Alembic,
  asyncpg, structlog
- Frontend: Vite, React 19, TypeScript strict, TanStack Query, TanStack Router,
  Zustand, Tailwind, shadcn/ui, Storybook, Biome, i18next
- Database: PostgreSQL 17
- Testing: pytest + pytest-asyncio + Testcontainers + Schemathesis (BE),
  Vitest + RTL + Playwright (FE)
- Task runner: Taskfile. No Make. No npm scripts for orchestration.
- When unsure about a library API, use Context7 to fetch current documentation
  rather than relying on training data.

## Sacred Rules

1. One class per file. Always. No exceptions. If you believe two classes belong
   together, stop and ask.
2. TDD. Always. Never write implementation before a failing test exists.
   Red → green → refactor.
3. No paradigm drift. One way to do each thing. If you think a second way is
   needed, stop and ask.
4. Run `task check` before declaring any work done. Never use `--no-verify`.

## Architecture

### Backend: Vertical Slices

app/core/ — config, database, logging
app/api/ — middleware, exception handler, health, shared deps
app/exceptions/ — DomainError hierarchy (base + _generated/)
app/shared/ — BaseRepository, BaseService, BaseModel
app/schemas/ — Page[T], ErrorResponse
app/types/ — Money, Currency
app/features/<feature>/ — model, repository, service, router, schemas/

### Frontend: Vertical Slices

src/routes/ — TanStack Router file-based routes
src/features/<feature>/api/ — TanStack Query hooks
src/features/<feature>/components/<name>/ — component + test + story
src/shared/ — components, hooks, lib, types
src/i18n/ — config + locales
src/stores/ — Zustand (client state only)
src/app/ — providers, error boundary

### Layer Rules (mechanically enforced)

- Features cannot import from other features.
- Backend: router → service → repository → model. No skipping.
- Schemas never import models. Models never import schemas.
- shared/ and core/ never import from features/.
- Frontend: features/ imports from shared/. shared/ cannot import features/.

## Forbidden Patterns — Backend

- Never use `print`. Use structlog.
- Never use `logging.getLogger`. Use structlog.
- Never use f-string log messages. Use structlog's key=value pairs:
  `logger.info("event_name", key=value)` not `logger.info(f"thing {value}")`.
- Never raise `HTTPException`. Raise a DomainError subclass.
- Never write a `try/except` that silently swallows errors. If you catch, re-raise or log.
- Never return `None` to signal "not found." Raise `NotFoundError`.
- Never edit files in `exceptions/_generated/`. Edit errors.yaml, run task errors:generate.
- Never use `os.environ` or `os.getenv`. Use pydantic-settings.
- Never use `datetime.now()` without tz=. Use `datetime.now(UTC)`.
- Never use `datetime.utcnow()`.
- Never use `TIMESTAMP` without time zone. Use `TIMESTAMPTZ`.
- Never use `float` for monetary values. Use Money.
- Never use SQLite as a test database. Use Testcontainers with real Postgres.
- Never mock the database in integration tests. Use real Postgres with transactional rollback.
- Never put business logic in route handlers. Handlers call one service method.
- Never put business logic in repositories. Repositories do data access only.
- Never put schema knowledge in repositories. Repositories return SQLAlchemy models.
- Never write a sync `def` route handler. All handlers are `async def`.
- Never use `run_in_executor` or mix sync/async code paths.
- Never use global singletons, service locators, or DI container libraries. Use FastAPI Depends().
- Never import services or repositories directly in handlers. Wire via Depends() factories.
- Never import from one feature into another feature. Features are independent.
- Never use schema factories or `model_validate` directly in route handlers.
- Never inherit schemas across entities. Each entity has its own schemas.
- Never import from `exceptions._generated` directly. Import from `exceptions`.
- Never add a feature without adding it to import-linter independence contract.
- Never add a model without adding its import to alembic/env.py.
- Never create a migration with more than one logical change.
- Never use `# type: ignore` without a comment explaining why.

## Forbidden Patterns — Frontend

- Never use raw strings in JSX. Use t() from useTranslation().
- Never use fetch() outside shared/lib/api-client.ts.
- Never make API calls outside TanStack Query hooks. All data fetching goes through
  features/*/api/ hooks that use the api-client wrapper.
- Never cache API data in Zustand. Server state belongs in TanStack Query.
- Never use useState for data that comes from the API. Use TanStack Query.
- Never display a raw error. Use <ErrorMessage> or <ErrorDisplay>.
- Never display a raw timestamp. Use <DateTime>.
- Never display a raw money value. Use <MoneyDisplay>.
- Never format dates or numbers manually. Use Intl.DateTimeFormat and Intl.NumberFormat
  wrappers in shared/lib/format.ts.
- Never use console.log in committed code. Use the logger wrapper.
- Never use Number arithmetic for money. Use dinero.js wrapper.
- Never introduce a second HTTP client library.
- Never write a component without a story.
- Never use snapshot tests.
- Never concatenate strings for translations. Use i18next interpolation.
- Never use <a href> for internal navigation. Use TanStack Router Link.
- Never put route guards in component bodies. Use beforeLoad.

## Forbidden Patterns — Cross-cutting

- Never add a top-level folder without updating this file and docs/decisions.md.
- Never write implementation before a failing test exists.
- Never commit without running task check.
- Never use --no-verify.
- Never add an env var without adding to both Settings and .env.example.
- Never add an error code without editing errors.yaml, running task errors:generate,
  adding translations to ALL languages, and running task errors:check.
- Never add a translation without adding to ALL languages in the same commit.
- Never skip a test level.
- Never introduce a new dependency without justification.
- Never write a test class. Use pytest functions (backend) or describe/it (frontend).
- Never use unittest.TestCase. Use pytest.
- Never write a test with no assertions.
- Never use React Context for state management. Only for dependency injection (providers).

## Naming Conventions

Python files: snake_case.py
Python classes: PascalCase with role suffix (WidgetService, WidgetRepository)
Python functions: snake_case verbs
Python tests: test_<unit>_<scenario>_<expected>
Frontend files: kebab-case.tsx / kebab-case.ts
Frontend components: PascalCase export (export function WidgetList)
Frontend hooks: useCamelCase
Frontend tests: describe("<Subject>", () => { it("<behavior>") })
E2E tests: test("<user-facing behavior>")
Storybook titles: UI/<Component> for shared, Features/<Feature>/<Component> for features
Migrations: <rev>_<slug>.py, snake_case slug, one change per migration

## Error System

Source of truth: packages/error-contracts/errors.yaml
Generate: task errors:generate (produces _generated/ Python files + TypeScript types)
Validate: task errors:check (validates translations match contracts)

To add a new error:
1. Add code to errors.yaml
2. Run task errors:generate
3. Add translation to ALL locales/*/errors.json
4. Run task errors:check
5. Write test that raises error and asserts response shape

## Testing Rules

Four levels, all mandatory:
1. Unit — no DB, no network. Fast (<10s).
2. Integration — real Postgres via Testcontainers. httpx.AsyncClient.
3. E2E — Playwright against full docker-compose. Few tests.
4. Contract — Schemathesis + generated client diff check.

Type checkers (Pyright strict, tsc strict) are build failures, not warnings.

Excluded: property-based, performance, mutation, snapshot, fuzz beyond Schemathesis.

## Conventions (no code in template)

File uploads: S3 interface, presigned URLs, never local disk, never DB,
validate MIME + size + magic bytes, strip EXIF.

WebSockets: endpoints in api/v1/ws/, envelope {type, payload, request_id},
ConnectionManager class, ticket-based auth.

Caching: interface first (get/set/delete), implementation second.
Rate limiting: interface first, per-route config.
Background jobs: job queue, never in request handlers.

## Dependabot

Close and delete any Dependabot PR that proposes a version older than latest.
Always use absolute latest versions for all dependencies.
```

---

## 13. Risks and Unknowns

1. **Testcontainers in CI.** GitHub Actions supports Docker but startup adds 15-30s. Mitigation: CI can use Postgres service container as fallback. conftest.py supports both modes.

2. **TanStack Router file-based routing + `$lang`.** Non-trivial Vite plugin setup. May need manual config. Budget iteration time in Phase 6.

3. **Biome + ESLint dual setup.** Two linters may conflict. ESLint config must be minimal (one plugin, one rule, `.tsx` only). Test early.

4. **Pyright strict + SQLAlchemy generics.** SQLAlchemy type stubs have gaps. 2-3 targeted `# type: ignore` in base class only. Each with comment.

5. **Error contracts codegen maintenance.** Custom script generating Python + TypeScript. Kept simple (string templates, no AST). Low maintenance burden.

6. **PR size.** Hundreds of files. Mitigation: phases as logical commits, Copilot review as second pass.

7. **i18next no-literal-string false positives.** ESLint rule flags CSS classes, aria labels. Needs careful ignore config. Budget tuning time.

8. **Translation completeness blocks incremental commits.** New error code + translations must be in same commit. Feature, not bug, but workflow adjustment.

---

## 14. Things Wrong With the Original Prompt

1. **Assumed envelope responses exist.** They don't. Backend is nearly empty. The reshape is a build, not a refactor.

2. **Specified `BaseRepository[ModelT, CreateT, ReadT, UpdateT]`.** Four type params push schema knowledge into the repository. Resolved: `BaseRepository[ModelT]` is correct.

3. **Specified health at `/api/v1/health`.** Versioned health breaks K8s probes. Resolved: root-level `/health` and `/ready`.

4. **"No router factory" is a tradeoff, not obvious.** The concrete router is ~30 lines of boilerplate. Some teams would reasonably argue for a factory. The prompt's decision is correct (explicit > magic for OpenAPI/Schemathesis) but it's a tradeoff.

5. **Original scope didn't include i18n, error contracts, money, or foundational concerns.** These were introduced in a follow-up document and roughly tripled the code volume. The original phase estimates were calibrated for the smaller scope.

6. **Nothing else.** Testing philosophy, TDD discipline, one-paradigm rule, "forbidden unless asked" tone — all correct.
