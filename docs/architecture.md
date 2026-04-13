# Architecture

## System Overview

```
                    +-----------+
                    |  Browser  |
                    +-----+-----+
                          |
                    /$lang/widgets
                          |
                    +-----v-----+
                    |  Frontend |    React 19, Vite, TanStack Router/Query
                    |  (SPA)    |    i18next, Zustand, Tailwind, shadcn/ui
                    +-----+-----+
                          |
                    /api/v1/widgets
                          |
                    +-----v-----+
                    |  Backend  |    FastAPI, Pydantic v2, structlog
                    |  (API)    |    SQLAlchemy 2.0 async, Alembic
                    +-----+-----+
                          |
                    +-----v-----+
                    | PostgreSQL|    17
                    |   (DB)    |
                    +-----------+
```

## Backend Architecture: Vertical Slices

```
app/
+-- core/               Config, database, logging. Cross-cutting infrastructure.
+-- api/                 Middleware, exception handler, health/ready, shared deps.
+-- exceptions/          DomainError base (base.py) + generated subclasses (_generated/).
+-- shared/              BaseRepository[ModelT], BaseService[M,C,R,U], base SQLAlchemy model.
+-- schemas/             Page[T], ErrorResponse. Shared response shapes.
+-- types/               Money, Currency. Value objects.
+-- features/
    +-- widget/          One folder per feature. Self-contained vertical slice.
        +-- model.py         SQLAlchemy model
        +-- repository.py    Data access (BaseRepository subclass)
        +-- service.py       Business logic (BaseService subclass)
        +-- router.py        HTTP endpoints (FastAPI APIRouter)
        +-- schemas/         Pydantic schemas (create, read, update)
```

### Layer Flow

```
HTTP Request
    |
    v
router.py       Thin handler. Declares Depends(), calls service, returns result.
    |
    v
service.py       Business logic. Validates, orchestrates, converts model <-> schema.
    |
    v
repository.py    Data access. SQLAlchemy queries. Raises NotFoundError/ConflictError.
    |
    v
model.py         SQLAlchemy model. Maps to database table.
```

No layer skipping. Router never touches the database. Repository never knows about schemas.

### Error Flow

```
Repository raises NotFoundError (generic, no params)
    |
    v
Service catches, re-raises WidgetNotFoundError(widget_id=...) (typed params)
    |
    v
Exception handler serializes to JSON: {error: {code, params, details, request_id}}
    |
    v
Frontend ApiError.is("WIDGET_NOT_FOUND") -> t("WIDGET_NOT_FOUND", {widget_id})
    |
    v
User sees: "Widget 'abc-123' was not found." (localized)
```

## Frontend Architecture: Vertical Slices

```
src/
+-- routes/              TanStack Router file-based routes. /$lang/ prefix.
+-- features/
|   +-- widgets/
|       +-- api/         TanStack Query hooks (useWidgets, useCreateWidget, ...)
|       +-- components/  Each component in its own folder with test + story
+-- shared/
|   +-- components/
|   |   +-- ui/          shadcn/ui generated components
|   |   +-- error-message/  <ErrorMessage> -- the only error renderer
|   |   +-- date-time/      <DateTime> -- the only timestamp renderer
|   |   +-- money-display/  <MoneyDisplay> -- the only money renderer
|   |   +-- language-switcher/
|   +-- hooks/           useCurrentLanguage
|   +-- lib/             api-client, money, format, logger, cn
|   +-- types/           ApiError re-export
+-- i18n/                Config + locales (en, ro). Error codes = i18n keys.
+-- stores/              Zustand. Client state only. Never caches API data.
+-- app/                 Providers, error boundary.
```

## API Versioning

- Business endpoints: `/api/v1/widgets`, `/api/v1/widgets/{id}`
- Health/readiness: `/health`, `/ready` (root, unversioned)

## Packages

- `packages/api-client/` -- Generated TypeScript types from backend OpenAPI spec.
- `packages/error-contracts/` -- Error code definitions (errors.yaml), codegen, validator.

## Infrastructure

- `infra/compose/` -- docker-compose for dev and prod.
- `infra/docker/` -- Dockerfiles for backend and frontend.
- `infra/terraform/` -- Structure and conventions only. Cloud-agnostic empty shells.
