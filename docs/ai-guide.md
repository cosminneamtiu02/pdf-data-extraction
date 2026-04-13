# AI Guide — Template Scaffold Overview

What is already implemented, what is not, and how the pieces connect.
Read `CLAUDE.md` for all rules and forbidden patterns.

## Backend — What's Built

**Core infrastructure** is fully implemented: app factory (`app/main.py`), configuration via pydantic-settings (`app/core/config.py`), async database engine with connection pooling (`app/core/database.py`), structured logging via structlog with JSON output in production (`app/core/logging.py`), and FastAPI dependency injection for settings and sessions (`app/api/deps.py`).

**Middleware stack** is complete: request ID generation and validation (UUID-only, prevents log injection), access logging with method/path/status/duration, security headers (HSTS, CSP, X-Frame-Options, Permissions-Policy, Referrer-Policy), and CORS with configurable origins. All wired in `app/api/middleware.py`.

**Error handling** is fully implemented via a code-generated system. Error codes live in `packages/error-contracts/errors.yaml` — a codegen script produces one Python exception class per error code in `app/exceptions/_generated/`, plus TypeScript types and a translation validation JSON. A single exception handler in `app/api/errors.py` maps all `DomainError` subclasses to a consistent JSON response shape: `{error: {code, params, details, request_id}}`. Error codes double as i18n lookup keys on the frontend.

**Generic CRUD abstractions** are implemented: `BaseRepository[ModelT]` handles create/get/list/update/delete with `NotFoundError` on missing entities and offset pagination returning `tuple[list[ModelT], int]`. `BaseService[ModelT, CreateT, ReadT, UpdateT]` wraps a repository, converts models to read schemas via `_to_read()`, and constructs `Page[ReadT]` responses. `Page[T]` is the pagination envelope with items/total/page/size/pages.

**Widget feature** is the working example of a complete vertical slice: SQLAlchemy model, repository, service with domain validation (name length, uniqueness), router with thin handlers, and three Pydantic schemas (create/read/update) each in its own file. It demonstrates the catch-and-re-raise pattern where the service catches generic `NotFoundError` from the repository and re-raises entity-specific `WidgetNotFoundError` with typed params.

**Money type** exists as a value object (`Money(amount_minor: int, currency: str)`) using integer minor units and ISO 4217 currency codes. Not used by Widget — it's ready for features that need monetary values.

**Health endpoints** are at root level (outside `/api/v1/`): `/health` for liveness and `/ready` for readiness (checks DB connectivity, returns 503 if unreachable).

**Alembic** is configured for async SQLAlchemy with one migration (`0001_create_widget_table`).

**Architecture enforcement** is mechanical: import-linter has 4 contracts — feature isolation (features can't import each other), feature layering (router → service → repository, no skipping), schemas can't import models, and shared/core can't import features.

## Frontend — What's Built

**Routing** uses TanStack Router with file-based routes. URLs are prefixed with `/$lang/` (e.g., `/en/widgets`). The `$lang` route validates the language param in `beforeLoad` — unsupported languages redirect to the default. The root layout (`__root.tsx`) renders a header with language switcher and an `<Outlet>`. Currently the only content route is `/$lang/widgets/`.

**API client** uses `openapi-fetch` with a generated schema from the backend's OpenAPI spec. All API calls are compile-time type-checked — URL paths, query params, request bodies, and response types are validated against `packages/api-client/src/schema.d.ts`. Error middleware on the client throws `ApiError` (server responded with error) or `NetworkError` (server unreachable), and injects `X-Request-ID` on every request.

**Data fetching** goes through TanStack Query hooks in `src/features/widgets/api/`. Five hooks exist: list (paginated), get by ID, create, update, delete. Each uses `apiClient.GET/POST/PATCH/DELETE` with types from `@repo/api-client`. Mutations invalidate the query cache on success.

**Widget components**: `WidgetList` renders four states — loading, empty, error (with `ErrorMessage` for API errors, translated fallback for network errors), and populated (with `DateTime` for timestamps). `WidgetForm` is a controlled form with name (required) + description (optional), uses `useCreateWidget`, clears on success, shows errors inline.

**Shared components** are all implemented with tests and Storybook stories: `ErrorMessage` (translates error codes via i18n), `ErrorDisplay` (wraps ErrorMessage with request ID), `DateTime` (locale-aware via `Intl.DateTimeFormat`), `MoneyDisplay` (locale-aware currency via `Intl.NumberFormat`), `LanguageSwitcher` (buttons for supported languages), and shadcn-style `Button` + `Input` with variant/size props.

**i18n** uses i18next with two namespaces: `common` (UI strings) and `errors` (error code translations). Two locales are set up: English and Romanian. Detection order: localStorage (`app.language`) → browser navigator → default `en`.

**State management**: server state is in TanStack Query. Client state goes in Zustand stores (`src/stores/` — empty, ready for use). No Zustand stores exist yet because the template has no client-only state.

**Shared utilities**: `cn()` for Tailwind class merging (clsx + tailwind-merge), `format.ts` with `Intl` wrappers for dates/numbers/currencies, `logger.ts` as a console wrapper, `money.ts` with dinero.js for frontend money arithmetic.

## Database — What's Built

PostgreSQL 17. One table: `widgets` with UUID primary key, `name` (unique, varchar 255), `description` (nullable text), `created_at` and `updated_at` (both timestamptz with server defaults). Base model provides UUID `id`, `created_at`, and `updated_at` to all entities. Naming convention enforces constraint prefixes: `pk_`, `fk_`, `uq_`, `ix_`, `ck_`.

## How Things Bind Together

**Backend → Database**: FastAPI depends on `get_session` which yields an `AsyncSession` from a cached `async_sessionmaker`. Repositories receive the session, services receive the repository, routers receive the service — all via `Depends()`.

**Frontend → Backend**: The frontend Vite dev server proxies `/api` to `localhost:8000`. In production, nginx serves the SPA and proxies API requests. The `openapi-fetch` client sends typed requests, and TanStack Query manages caching, refetching, and loading states.

**Error contracts bind FE ↔ BE**: `errors.yaml` generates Python exception classes (backend raises them), TypeScript types (frontend knows the codes), and a `required-keys.json` that validates all error codes have translations in every locale. The error code IS the i18n key — `t("errors:WIDGET_NOT_FOUND", {widget_id})` renders a localized message.

**Type safety binds FE ↔ BE**: `task client:generate` extracts the OpenAPI spec from FastAPI and generates `schema.d.ts`. The frontend hooks import types from this file. If the backend changes a field name, the frontend gets a compile error. CI verifies the generated file is committed and up to date.

## What Is NOT Built — TODOs

### No authentication or authorization
No auth middleware, no login, no JWT, no OAuth, no session management, no route guards, no RBAC, no permissions. The API is fully open. This is the first thing to implement on a real project.

### No real domain model
Widget is a demo entity. It should be replaced or supplemented with the project's actual domain entities following the same vertical-slice pattern.

### No E2E tests in CI
A placeholder Playwright spec exists at `tests/e2e/widget-crud.spec.ts` but Playwright is not in the CI pipeline. E2E needs a running full stack (docker-compose) which CI doesn't spin up yet.

### No deployment
`deploy.yml` has TODO stubs for pushing Docker images and deploying. The container registry and deployment target need to be configured per project.

### No cloud infrastructure
Terraform files are empty shells (`backend.tf.example`) for dev/staging/prod. No cloud provider is chosen.

### No file uploads
Convention is defined in `CLAUDE.md` (S3 interface, presigned URLs, MIME + magic bytes validation, EXIF stripping) but nothing is implemented.

### No WebSockets
Convention is defined (envelope `{type, payload, request_id}`, `ConnectionManager`, ticket-based auth) but nothing is implemented.

### No caching layer
No Redis, no Memcached, no cache interface. Convention says interface-first (get/set/delete), implementation second.

### No rate limiting
Convention says per-route config, add between RequestId and AccessLog middleware. Nothing is implemented.

### No background jobs
Convention says job queue (not in request handlers). No Celery, no ARQ, nothing is wired.

### No email/notifications
Nothing exists.

### Storybook stories are incomplete
Widget stories exist but only have a `Default` variant. The plan calls for Empty/WithWidgets/Loading/Error variants for WidgetList and Default/Submitting/WithError for WidgetForm. These require MSW (Mock Service Worker) or module mocking to simulate hook states, neither of which is installed.

### CSP is permissive
The Content-Security-Policy in middleware allows `unsafe-inline` for scripts and styles (needed for Vite/Tailwind dev). Should be tightened per project.

### OpenAPI docs disabled in production
Swagger UI and ReDoc are only available in development. Enable in `main.py` if the project needs public API documentation.
