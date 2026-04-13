# Conventions

Rules that govern how code is written in this repository. See `CLAUDE.md` for the
enforcement version. This document provides rationale.

## File Naming

| Context | Convention | Example |
|---|---|---|
| Python files | `snake_case.py` | `widget_service.py` |
| Python classes | `PascalCase` + role suffix | `WidgetService`, `WidgetCreate` |
| Python functions | `snake_case` verbs | `get_by_id`, `create_widget` |
| Frontend files (all) | `kebab-case.tsx` / `.ts` | `widget-list.tsx`, `use-widgets.ts` |
| Frontend component exports | `PascalCase` | `export function WidgetList` |
| Frontend hooks | `useCamelCase` | `useWidgets`, `useCurrentLanguage` |
| Feature folders | `kebab-case` | `features/widgets/` |
| Component folders | `kebab-case` matching component | `widget-list/widget-list.tsx` |

## Component File Structure

Every frontend component lives in its own folder:

```
component-name/
  component-name.tsx          # The component
  component-name.test.tsx     # Co-located test
  component-name.stories.tsx  # Co-located Storybook story
```

Exception: shadcn/ui components in `shared/components/ui/` stay flat unless they
have tests.

## Test Naming

| Context | Convention | Example |
|---|---|---|
| Python | `test_<unit>_<scenario>_<expected>` | `test_widget_service_create_returns_widget_read` |
| Vitest | `describe("<Subject>", () => it("<behavior>"))` | `describe("WidgetList", () => it("renders empty state"))` |
| Playwright | `test("<user-facing behavior>")` | `test("user can create a widget and see it in the list")` |

## Test File Location

- **Backend:** `tests/unit/` and `tests/integration/` mirror the source tree.
  `app/features/widget/service.py` -> `tests/unit/features/widget/test_widget_service.py`.
- **Frontend:** co-located. `features/widgets/components/widget-list/widget-list.test.tsx`.
- **E2E:** `tests/e2e/` in the frontend directory.

## Pydantic Schemas

Three schemas per entity, one class per file:
- `<entity>_create.py` -- fields the client sends to create (no id, no timestamps)
- `<entity>_read.py` -- fields the client receives (includes id, timestamps)
- `<entity>_update.py` -- fields the client sends to update (all optional for PATCH)

Schemas never import models. Conversion happens in the service layer via `_to_read()`.

## SQLAlchemy

- All models inherit from `shared/base_model.py` which provides `id` (UUID),
  `created_at` (TIMESTAMPTZ), `updated_at` (TIMESTAMPTZ).
- MetaData naming convention is explicit: `pk_`, `fk_`, `uq_`, `ix_`, `ck_` prefixes.
- All timestamp columns use `TIMESTAMPTZ`. `TIMESTAMP` without time zone is banned.

## Alembic Migrations

- One logical change per migration.
- Slug is `snake_case`: `0001_create_widget_table.py`.
- Every new model must be imported in `alembic/env.py` for autogenerate to detect it.

## Error System

- Error codes defined in `packages/error-contracts/errors.yaml` (single source of truth).
- Codegen produces one file per error class in `exceptions/_generated/`.
- Error code = i18n lookup key in the `errors` namespace.
- Translations validated at build time. Missing translation = build error.

## Dependencies

- Always use absolute latest versions.
- Close/delete Dependabot PRs that propose older versions.
- Every new dependency requires justification.

## Environment Variables

- All config via `pydantic-settings` in `core/config.py`.
- Every new env var added to both `Settings` class and `.env.example` in the same commit.
- Production rejects development defaults (CORS `*`, debug mode).
