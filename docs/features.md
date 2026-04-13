# Features Catalog

Every feature already implemented in this template, each with a short description.
For exhaustive file-by-file detail, see [implementation-reference.md](implementation-reference.md).

`★ Insight ─────────────────────────────────────`
This file is a flat, discoverable index of what already exists — useful both for humans onboarding and for AI agents deciding whether to reuse vs. build. It deliberately avoids how-tos; it answers "what's in the box" only.
`─────────────────────────────────────────────────`

---

## Backend — Core Infrastructure

### Typed Settings ([app/core/config.py](apps/backend/app/core/config.py))
Pydantic-settings based configuration with a required `DATABASE_URL` and sensible defaults for `APP_ENV`, `LOG_LEVEL`, and `CORS_ORIGINS`. A validator rejects any non-`postgresql://` database URL at startup, so misconfigured environments fail fast instead of crashing mid-request.

### Async Database Engine & Sessions ([app/core/database.py](apps/backend/app/core/database.py))
SQLAlchemy 2.0 async engine with `pool_pre_ping`, bounded pool size, and a Postgres-level 30s statement timeout. The `get_session` FastAPI dependency yields an `AsyncSession`, commits on success, rolls back on exceptions, and the engine is disposed cleanly on app shutdown.

### Structured Logging ([app/core/logging.py](apps/backend/app/core/logging.py))
Structlog pipeline with contextvar merging, ISO timestamps, and JSON output in production / console output in dev. Noisy loggers (`uvicorn.access`, `sqlalchemy.engine`) are silenced at WARNING so real signal isn't buried.

### Request ID Middleware ([app/api/middleware.py](apps/backend/app/api/middleware.py))
Validates incoming `X-Request-ID` headers against a UUID regex, generates a fresh UUID4 if missing or malformed, and binds it into structlog contextvars so every log line in the request scope is correlatable. The ID is echoed back in the response header and injected into every error body.

### Access Log Middleware ([app/api/middleware.py](apps/backend/app/api/middleware.py))
Emits one `http_request` structlog event per request with method, path, status code, and `perf_counter`-measured duration in milliseconds. Runs inside the request-id middleware so each line already carries the request ID.

### Security Headers Middleware ([app/api/middleware.py](apps/backend/app/api/middleware.py))
Adds HSTS (1 year + subdomains), `X-Content-Type-Options: nosniff`, strict referrer policy, `X-Frame-Options: DENY`, a restrictive `Permissions-Policy`, and a baseline CSP to every response. These are cheap, blanket-applied defaults intended to be tightened per-project.

### CORS ([app/api/middleware.py](apps/backend/app/api/middleware.py))
Standard FastAPI `CORSMiddleware` with origins driven by the `CORS_ORIGINS` setting (JSON-parsed list). Credentials are enabled and all methods/headers are allowed — adjust for stricter production needs.

### Health & Readiness Endpoints ([app/api/health_router.py](apps/backend/app/api/health_router.py))
`GET /health` is a pure liveness probe returning `{"status":"ok"}`. `GET /ready` executes `SELECT 1` against the database and returns 503 if the round-trip fails, so orchestrators won't route traffic to a pod whose DB is unreachable.

---

## Backend — Error System

### DomainError Hierarchy ([app/exceptions/base.py](apps/backend/app/exceptions/base.py))
Single `DomainError` base class carrying a `code: ClassVar[str]` and `http_status: ClassVar[int]`, plus an optional typed Pydantic `params` model. Only the code is stored in `args` so PII in params never accidentally ends up in stack traces.

### Generated Error Classes ([app/exceptions/_generated/](apps/backend/app/exceptions/_generated/))
Every error code in `errors.yaml` is code-generated into its own Python file with a typed params model (where applicable), enforcing the one-class-per-file rule. A `_registry.py` maps error code strings back to classes for handler lookup.

### Exception Handlers ([app/api/errors.py](apps/backend/app/api/errors.py))
Three handlers serialize `DomainError`, `RequestValidationError`, and unhandled `Exception` into the same `{error: {code, params, details, request_id}}` envelope. This guarantees every error the client sees is shape-identical regardless of where it originated.

### Error Contracts Package ([packages/error-contracts/](packages/error-contracts/))
Single source of truth: `errors.yaml` drives a Python codegen step (classes + registry), a TypeScript codegen step (`ErrorCode` union + `ErrorParamsByCode` map), and a translation validator that ensures every locale has every key with matching interpolation params. Adding an error is one yaml edit + `task errors:generate` + translations in all languages.

---

## Backend — Shared Abstractions

### BaseModel ([app/shared/base_model.py](apps/backend/app/shared/base_model.py))
SQLAlchemy declarative base with a strict constraint naming convention (`pk_`, `fk_`, `uq_`, `ix_`, `ck_`) and three common columns: UUID `id`, `created_at`, and `updated_at` — all TIMESTAMPTZ with server defaults. Every entity in the project inherits this, so migrations and indexes have predictable names.

### BaseRepository[ModelT] ([app/shared/base_repository.py](apps/backend/app/shared/base_repository.py))
Generic async CRUD repository with `create`, `get_by_id`, `list(page, size)`, `update`, and `delete`. `get_by_id` raises `NotFoundError` instead of returning `None`, and `list` returns a `(items, total)` tuple so services can build `Page[T]` responses.

### BaseService[ModelT, CreateT, ReadT, UpdateT] ([app/shared/base_service.py](apps/backend/app/shared/base_service.py))
Generic abstract service that wraps a repository and requires subclasses to implement `_to_read(model) -> ReadT`. Both `create` and `update` use Pydantic's `model_dump(exclude_unset=True)` so PATCH semantics are correct by default.

### Page[T] Schema ([app/schemas/page.py](apps/backend/app/schemas/page.py))
Generic pagination envelope with `items`, `total`, `page`, `size`, and a computed `pages` count. The `create()` classmethod is the only way to construct it, which guarantees the page count is always in sync with total/size.

### Money Value Object ([app/types/money.py](apps/backend/app/types/money.py))
Frozen Pydantic model with `amount_minor: int` and an ISO 4217 `currency: str`, validated against the full 172-code list. A `model_validator` rejects `float` inputs entirely, making it impossible to accidentally introduce floating-point rounding into monetary logic.

### Error Response Schemas ([app/schemas/error_response.py](apps/backend/app/schemas/error_response.py))
`ErrorDetail`, `ErrorBody`, and `ErrorResponse` split across three files (one class each) to satisfy the sacred one-class-per-file rule. These are used only for OpenAPI documentation; runtime error bodies are constructed by the exception handlers directly.

---

## Backend — Widget Feature (Reference Slice)

### Widget Model, Repository, Service, Router ([app/features/widget/](apps/backend/app/features/widget/))
Full vertical slice demonstrating the architecture: model with a unique `name` column, repository inheriting `BaseRepository` with zero overrides, service mapping `IntegrityError` on `uq_widgets_name` to `WidgetNameConflictError`, and a router exposing POST/GET/LIST/PATCH/DELETE under `/api/v1/widgets`. Name-length validation lives in the service layer (not the schema) specifically to emit a richer typed error.

### Widget Schemas ([app/features/widget/schemas/](apps/backend/app/features/widget/schemas/))
`WidgetCreate`, `WidgetRead`, and `WidgetUpdate` live in separate files with no cross-entity inheritance. `WidgetUpdate` makes every field optional to match PATCH semantics, while `WidgetRead` uses `from_attributes=True` to map directly from the ORM model.

---

## Backend — Architecture Enforcement

### Import-Linter Contracts ([apps/backend/architecture/import-linter-contracts.ini](apps/backend/architecture/import-linter-contracts.ini))
Four contracts mechanically enforce the layering rules: features cannot import each other, schemas cannot import models, shared/core/schemas/types cannot import features, and within a feature router→service→repository is the only legal direction. Violations fail `task check:arch` in CI.

### Database Migrations ([apps/backend/alembic/](apps/backend/alembic/))
Alembic configured for async SQLAlchemy with a single initial migration creating the `widgets` table. Migrations run in CI via Testcontainers, so every PR is validated against a real Postgres schema.

---

## Backend — Tests

### Unit Tests (18) ([apps/backend/tests/unit/](apps/backend/tests/unit/))
Fast, DB-free tests covering Settings validation, Money constraints, `Page` math, domain error construction, error-handler serialization, and `WidgetService` with a fake repository. These run in well under 10 seconds as the primary TDD feedback loop.

### Integration Tests (34) ([apps/backend/tests/integration/](apps/backend/tests/integration/))
Full Testcontainers Postgres 17 setup with session-scoped container/engine and per-test `TRUNCATE CASCADE` cleanup. Covers full CRUD lifecycles, pagination, duplicate detection, 404/409/422 error envelopes, request-id propagation, security headers, and CORS configuration.

### Contract Tests (2) ([apps/backend/tests/contract/](apps/backend/tests/contract/))
Validates the generated OpenAPI spec shape and asserts `/health` conforms to it. Paired with the `api-client-checks` CI job, this guarantees the frontend's generated types can't silently drift from the backend API.

---

## Frontend — App Shell

### Entry Point & Providers ([src/main.tsx](apps/frontend/src/main.tsx), [src/app/providers.tsx](apps/frontend/src/app/providers.tsx))
The entry wires `StrictMode → ErrorBoundary → Providers → RouterProvider` in a deliberate order so errors during provider init still render something. `QueryClient` is configured with `staleTime: 60s` and a retry policy that skips retries on `ApiError` (server said no, stop asking).

### Error Boundary ([src/app/error-boundary.tsx](apps/frontend/src/app/error-boundary.tsx))
React class-component boundary with a translated fallback (title, message, reload button) using i18n keys. Logs the error via the shared logger wrapper, so production builds don't spam `console.error`.

---

## Frontend — Routing (TanStack Router, File-Based)

### Root Layout & Index Redirect ([src/routes/__root.tsx](apps/frontend/src/routes/__root.tsx), [src/routes/index.tsx](apps/frontend/src/routes/index.tsx))
Root layout renders the app header with the `LanguageSwitcher` and an `<Outlet>`. The index route unconditionally redirects to `/$lang/widgets` with the default language, so every URL the user ever sees carries an explicit language prefix.

### Language-Scoped Routes ([src/routes/$lang/route.tsx](apps/frontend/src/routes/$lang/route.tsx))
`beforeLoad` validates the `$lang` param against `SUPPORTED_LANGUAGES`, redirects invalid values to the default language, and calls `i18n.changeLanguage(lang)` before rendering. This keeps URL and i18n state guaranteed-in-sync without any `useEffect` hacks.

### Widgets Page ([src/routes/$lang/widgets/index.tsx](apps/frontend/src/routes/$lang/widgets/index.tsx))
Composes `<WidgetForm>` and `<WidgetList>` under a translated page title. This is the only reference page; new pages are added by dropping files into `src/routes/$lang/`.

---

## Frontend — API Client

### Typed API Client ([src/shared/lib/api-client.ts](apps/frontend/src/shared/lib/api-client.ts))
`openapi-fetch` client instantiated with generated `paths` types from `@repo/api-client`, giving compile-time errors on wrong URLs, methods, params, or response shapes. A middleware injects `X-Request-ID`, converts non-2xx bodies into `ApiError`, and wraps non-JSON failures (e.g. a 502 HTML page) as `NetworkError` so UI never crashes on bad bytes.

### ApiError / NetworkError Classes ([src/shared/lib/api-client.ts](apps/frontend/src/shared/lib/api-client.ts))
`ApiError` carries the typed `code`, `params`, `details`, `requestId`, and `httpStatus`, plus an `is(code)` type-narrowing helper. `NetworkError` is the catch-all for anything that never reached the application layer — these two classes are the only error types UI code needs to distinguish.

---

## Frontend — Widget Feature (Reference Slice)

### Widget Hooks ([src/features/widgets/api/](apps/frontend/src/features/widgets/api/))
Five hooks — `useWidgets`, `useWidget`, `useCreateWidget`, `useUpdateWidget`, `useDeleteWidget` — each a thin wrapper around `apiClient.GET/POST/PATCH/DELETE` with stable query keys. Mutations invalidate `["widgets"]` so lists refresh automatically after writes.

### WidgetList Component ([src/features/widgets/components/widget-list/](apps/frontend/src/features/widgets/components/widget-list/))
Handles all five canonical states: loading, `ApiError`, `NetworkError`, empty, and populated. Each widget card uses `<DateTime>` for `created_at` so locale formatting is consistent across the app.

### WidgetForm Component ([src/features/widgets/components/widget-form/](apps/frontend/src/features/widgets/components/widget-form/))
Controlled form using the shared `<Button>` and `<Input>` primitives with client-side empty-name validation and a disabled state while the mutation is pending. On error, routes `ApiError` into `<ErrorMessage>` (i18n-aware) and other failures into a translated network-error div.

---

## Frontend — Shared Components

### ErrorMessage ([src/shared/components/error-message/](apps/frontend/src/shared/components/error-message/))
Translates an `ApiError.code` via the i18n `errors` namespace with `params` interpolation, so a `WIDGET_NOT_FOUND` with `widget_id=abc` becomes `"Widget 'abc' was not found."` in EN or the RO equivalent. The request ID is always rendered below the message for support handoff.

### ErrorDisplay ([src/shared/components/error-display/](apps/frontend/src/shared/components/error-display/))
Centered container wrapper around `<ErrorMessage>` for section-level empty/error states. Used when an entire panel fails to load, versus inline form errors.

### DateTime ([src/shared/components/date-time/](apps/frontend/src/shared/components/date-time/))
Renders a `<time>` element with `Intl.DateTimeFormat` in the current i18n locale. Forbidden pattern #7 (no raw timestamps) means every displayed timestamp in the app must go through this component.

### MoneyDisplay ([src/shared/components/money-display/](apps/frontend/src/shared/components/money-display/))
Formats an `(amount_minor, currency)` pair via `Intl.NumberFormat` in the current locale, mirroring the backend `Money` value object. No floating-point math ever touches the value.

### LanguageSwitcher ([src/shared/components/language-switcher/](apps/frontend/src/shared/components/language-switcher/))
Renders one button per supported language and calls `i18n.changeLanguage` on click. The root layout places it in the header so it's always reachable.

### UI Primitives — Button, Input ([src/shared/components/ui/](apps/frontend/src/shared/components/ui/))
Shadcn-style primitives using the `cn()` class-merge utility, with Button supporting four variants and three sizes. Intentionally minimal — this is the seed set for building out shadcn components as needed.

---

## Frontend — Shared Utilities

### cn ([src/shared/lib/cn.ts](apps/frontend/src/shared/lib/cn.ts))
`clsx` + `tailwind-merge` one-liner used by every component that composes class names. Ensures conflicting Tailwind utilities resolve deterministically (last-one-wins).

### format ([src/shared/lib/format.ts](apps/frontend/src/shared/lib/format.ts))
`formatDate`, `formatNumber`, `formatCurrency` — thin wrappers over the `Intl.*` APIs. Forbidden pattern #9 (no manual formatting) means every locale-sensitive display goes through this file.

### logger ([src/shared/lib/logger.ts](apps/frontend/src/shared/lib/logger.ts))
`logger.info/warn/error` — a console wrapper that becomes a no-op in production builds. Replaces `console.log`, which is forbidden in committed code.

### money ([src/shared/lib/money.ts](apps/frontend/src/shared/lib/money.ts))
`Money` interface plus `createMoney` and `formatMoney` helpers. Matches the backend `Money` shape so values crossing the wire can be used directly.

---

## Frontend — i18n

### i18next Configuration ([src/i18n/](apps/frontend/src/i18n/))
Detection order: `localStorage` (key `app.language`) → `navigator` → default `"en"`. Two namespaces: `common` (UI strings) and `errors` (error code translations keyed by `ErrorCode` with interpolation params matching the backend).

### Locales — EN, RO ([src/i18n/locales/](apps/frontend/src/i18n/locales/))
Both languages ship with `common.json` and `errors.json` and are validated for key/param parity by `task errors:check`. Missing a translation in any language fails CI.

---

## Frontend — Tests

### Test Utilities ([src/test-utils.tsx](apps/frontend/src/test-utils.tsx))
Separate `testI18n` instance with all locale resources, a `createTestQueryClient` with no retries / zero gc, and `renderWithProviders` that wires both together. Every component test uses this instead of the production providers for speed and isolation.

### Component Tests (23 tests) ([src/**/*.test.tsx](apps/frontend/src/))
Vitest + React Testing Library covering the API client, Money helpers, all shared components (ErrorMessage/ErrorDisplay/DateTime/MoneyDisplay/LanguageSwitcher), and both widget components (list: empty/populated/loading/error; form: valid submit, empty-name guard, pending-disabled). Coverage threshold is 80% lines on testable files.

---

## Packages

### @repo/api-client ([packages/api-client/](packages/api-client/))
Generated TypeScript types produced by `openapi-typescript` from the backend's live OpenAPI spec. The generate step runs in CI and a `git diff --exit-code` on `schema.d.ts` means a PR that changes the backend API without regenerating the client fails the build.

### @repo/error-contracts ([packages/error-contracts/](packages/error-contracts/))
Houses `errors.yaml` (source of truth), three Python codegen generators (Python classes, TypeScript types, required-keys JSON), a translation validator, and 12 tests covering codegen and validation. This package is the seam that guarantees backend error shapes, frontend types, and translation files never drift.

---

## Infrastructure

### Backend Dockerfile ([infra/docker/backend.Dockerfile](infra/docker/backend.Dockerfile))
Two-stage build using `uv` from `ghcr.io/astral-sh/uv` in the builder for fast dependency installs, runtime on `python:3.13-slim` as a non-root user. Includes a `HEALTHCHECK` hitting `/health` so orchestrators detect broken containers.

### Frontend Dockerfiles ([infra/docker/](infra/docker/))
`frontend.Dockerfile` is a two-stage production build (node builder → nginx runtime) with a custom `nginx.conf` doing SPA fallback and reverse-proxying `/api/` and health endpoints to the backend. `frontend.dev.Dockerfile` is a dev-only image that installs pnpm deps at build time so the container doesn't need network access at runtime.

### Docker Compose ([infra/compose/](infra/compose/))
`docker-compose.yml` runs the full dev stack (db, backend with hot reload, frontend) and `docker-compose.prod.yml` runs the production variant with 4 uvicorn workers and `restart: always`. A gitignored `docker-compose.override.yml` is supported for local port remapping and host-gateway workarounds.

### Terraform Scaffold ([infra/terraform/](infra/terraform/))
Empty Terraform skeleton with `required_version >= 1.5.0`, a `modules/` directory, and commented AWS S3 / GCP GCS backend examples for dev/staging/prod. No provider is declared — that's a per-project decision.

---

## CI/CD

### CI Workflow ([.github/workflows/ci.yml](.github/workflows/ci.yml))
Four parallel jobs: `backend-checks` (ruff + pyright + import-linter + pytest all levels with 80% coverage + contract), `frontend-checks` (biome + eslint i18n + tsc + vitest + storybook build), `api-client-checks` (regenerates types and fails on diff), `error-contracts` (codegen + diff + translation validation). Every one is a hard gate.

### Deploy Workflow ([.github/workflows/deploy.yml](.github/workflows/deploy.yml))
Triggered on push to main, builds both Docker images tagged with the commit SHA. Push and deploy steps are intentional TODO stubs so you wire them to your actual registry/cluster.

### Copilot Review & Dependabot ([.github/](.github/))
Copilot is auto-requested as a PR reviewer via a workflow (best-effort, `continue-on-error`). Dependabot has 6 ecosystems wired (pip × 2, npm × 2, github-actions, terraform), all weekly except terraform (monthly).

---

## Tooling

### Taskfile ([Taskfile.yml](Taskfile.yml))
Single orchestration entry point with `dev`, `check` (lint → types → arch → test → errors), all test levels, db migration commands, client generation, errors generation/check, and docker commands. The rule is simple: if you find yourself typing a long command twice, add a task.

### Pre-commit Hooks ([.pre-commit-config.yaml](.pre-commit-config.yaml))
Pre-commit: whitespace/EOF/yaml/json/large-file checks + ruff fix + ruff format + biome check. Pre-push: pytest unit + vitest unit. Fast enough not to be skipped, strict enough to catch the obvious.

### Editor & VCS Config ([.editorconfig](.editorconfig), [.gitattributes](.gitattributes), [.tool-versions](.tool-versions))
LF line endings everywhere, 4-space Python and 2-space everything else, generated files marked `linguist-generated`, and pinned tool versions for Python/Node/pnpm so `asdf`/`mise` users get a consistent environment on first clone.
