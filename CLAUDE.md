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
   Red -> green -> refactor.
3. No paradigm drift. One way to do each thing. If you think a second way is
   needed, stop and ask.
4. Run `task check` before declaring any work done. Never use `--no-verify`.

## Architecture

### Backend: Vertical Slices

```
app/core/           -- config, database, logging
app/api/            -- middleware, exception handler, health, shared deps
app/exceptions/     -- DomainError hierarchy (base + _generated/)
app/shared/         -- BaseRepository, BaseService, BaseModel
app/schemas/        -- Page[T], ErrorResponse
app/types/          -- Money, Currency
app/features/<feature>/ -- model, repository, service, router, schemas/
```

### Frontend: Vertical Slices

```
src/routes/                             -- TanStack Router file-based routes
src/features/<feature>/api/             -- TanStack Query hooks
src/features/<feature>/components/<name>/ -- component + test + story
src/shared/                             -- components, hooks, lib, types
src/i18n/                               -- config + locales
src/stores/                             -- Zustand (client state only)
src/app/                                -- providers, error boundary
```

### Layer Rules (mechanically enforced)

- Features cannot import from other features.
- Backend: router -> service -> repository -> model. No skipping.
- Schemas never import models. Models never import schemas.
- shared/ and core/ never import from features/.
- Frontend: features/ imports from shared/. shared/ cannot import features/.

## Forbidden Patterns -- Backend

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

## Forbidden Patterns -- Frontend

- Never use raw strings in JSX. Use t() from useTranslation().
- Never use fetch() outside shared/lib/api-client.ts.
- Never make API calls outside TanStack Query hooks. All data fetching goes through
  features/*/api/ hooks that use the api-client wrapper.
- Never cache API data in Zustand. Server state belongs in TanStack Query.
- Never use useState for data that comes from the API. Use TanStack Query.
- Never display a raw error. Use `<ErrorMessage>` or `<ErrorDisplay>`.
- Never display a raw timestamp. Use `<DateTime>`.
- Never display a raw money value. Use `<MoneyDisplay>`.
- Never format dates or numbers manually. Use Intl.DateTimeFormat and Intl.NumberFormat
  wrappers in shared/lib/format.ts.
- Never use console.log in committed code. Use the logger wrapper.
- Never use Number arithmetic for money. Use dinero.js wrapper.
- Never introduce a second HTTP client library.
- Never write a component without a story.
- Never use snapshot tests.
- Never concatenate strings for translations. Use i18next interpolation.
- Never use `<a href>` for internal navigation. Use TanStack Router Link.
- Never put route guards in component bodies. Use beforeLoad.

## Forbidden Patterns -- Cross-cutting

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

- Python files: `snake_case.py`
- Python classes: `PascalCase` with role suffix (`WidgetService`, `WidgetRepository`)
- Python functions: `snake_case` verbs
- Python tests: `test_<unit>_<scenario>_<expected>`
- Frontend files: `kebab-case.tsx` / `kebab-case.ts`
- Frontend components: `PascalCase` export (`export function WidgetList`)
- Frontend hooks: `useCamelCase`
- Frontend tests: `describe("<Subject>", () => { it("<behavior>") })`
- E2E tests: `test("<user-facing behavior>")`
- Storybook titles: `UI/<Component>` for shared, `Features/<Feature>/<Component>` for features
- Migrations: `<rev>_<slug>.py`, `snake_case` slug, one change per migration

## Error System

Source of truth: `packages/error-contracts/errors.yaml`
Generate: `task errors:generate` (produces `_generated/` Python files + TypeScript types)
Validate: `task errors:check` (validates translations match contracts)

To add a new error:
1. Add code to errors.yaml
2. Run task errors:generate
3. Add translation to ALL locales/*/errors.json
4. Run task errors:check
5. Write test that raises error and asserts response shape

## Testing Rules

Four levels, all mandatory:
1. **Unit** -- no DB, no network. Fast (<10s).
2. **Integration** -- real Postgres via Testcontainers. httpx.AsyncClient.
3. **E2E** -- Playwright against full docker-compose. Few tests.
4. **Contract** -- Schemathesis + generated client diff check.

Type checkers (Pyright strict, tsc strict) are build failures, not warnings.

Excluded: property-based, performance, mutation, snapshot, fuzz beyond Schemathesis.

## Conventions (no code in template)

- **File uploads:** S3 interface, presigned URLs, never local disk, never DB,
  validate MIME + size + magic bytes, strip EXIF.
- **WebSockets:** endpoints in api/v1/ws/, envelope {type, payload, request_id},
  ConnectionManager class, ticket-based auth.
- **Caching:** interface first (get/set/delete), implementation second.
- **Rate limiting:** interface first, per-route config.
- **Background jobs:** job queue, never in request handlers.

## Dependabot

Close and delete any Dependabot PR that proposes a version older than latest.
Always use absolute latest versions for all dependencies.

**Auto-merge architecture** (see [docs/automerge.md](docs/automerge.md) for the full explainer):

- Dependabot-authored PRs that pass all required status checks are automatically
  squash-merged by [.github/workflows/dependabot-automerge.yml](.github/workflows/dependabot-automerge.yml).
  This is the ONE exception to the manual-Squash-button rule, documented in
  [docs/decisions.md ADR-010](docs/decisions.md).
- Never click merge on a green Dependabot PR. Let auto-merge handle it. If it's
  not auto-merging, something is wrong -- fix the root cause rather than merging
  manually.
- Never auto-merge a non-Dependabot PR. The workflow's `if:` guard scopes the
  exception strictly via `github.event.pull_request.user.login == 'dependabot[bot]'`.
  Human PRs merge manually via the green Squash button, always.
- Never use `github.actor` in any auto-merge guard condition. It reads the event
  triggerer, not the PR author, and will silently skip the workflow whenever a
  human interacts with a Dependabot PR (e.g. clicks "Update branch"). Always read
  `github.event.pull_request.user.login`.
- Never set `DEPENDABOT_AUTOMERGE_ENABLED` to `"true"` until the `main-protection`
  ruleset exists AND has all 4 (eventually 5) required status checks configured.
  `gh pr merge --auto` waits only for the checks declared on the ruleset; with no
  ruleset, `--auto` has nothing to wait for and merges immediately including red
  PRs. This was incident PR #19 on 2026-04-12.
- Never bypass the ruleset. Never add anyone (including yourself) to the bypass
  list. Never disable the workflow with `--no-verify` or equivalent. If auto-merge
  is misbehaving, flip the variable to `"false"` (`gh variable set
  DEPENDABOT_AUTOMERGE_ENABLED --body "false"`) to disable it cleanly.

**Handling broken Dependabot PRs:**

- The template ships [.github/workflows/dependabot-lockfile-sync.yml](.github/workflows/dependabot-lockfile-sync.yml)
  which auto-fixes the pnpm/uv lockfile-gap bug on Dependabot PRs once the repo
  variable `DEPENDABOT_LOCKFILE_SYNC_ENABLED` is set to `"true"` and the repo
  secret `DEPENDABOT_LOCKFILE_SYNC_PAT` contains a fine-grained PAT. With both
  in place (see [docs/new-project-setup.md Phase 5b](docs/new-project-setup.md)),
  broken Dependabot PRs self-heal within ~2 minutes of opening. Never disable
  the sync workflow except via the `DEPENDABOT_LOCKFILE_SYNC_ENABLED` variable
  kill switch.
- If the sync workflow is not enabled OR has failed AND a Dependabot PR touches
  only `package.json` and not `pnpm-lock.yaml` (or `pyproject.toml` and not
  `uv.lock`), CI will reject it with a frozen-lockfile error. Do not try to fix
  the PR in place. Close it, run `pnpm update --latest <packages>` (or the `uv`
  equivalent) locally, commit manifest + lockfile atomically, open a replacement
  PR. This is the lockfile-gap bug documented in
  [docs/automerge.md](docs/automerge.md#the-dependabot-lockfile-gap--fixed-at-template-level-by-the-sync-workflow).
- Never use `GITHUB_TOKEN` to push lockfile fixes from a workflow. `GITHUB_TOKEN`-
  authored pushes do not trigger subsequent workflow runs, so CI will not re-run
  on the fixed commit and the PR will stay stuck. Always use a PAT (or a GitHub
  App installation token). This is enforced by the sync workflow's design.
- If a Dependabot PR is `BEHIND` main (stale base), never click "Update branch"
  in the UI -- it attributes the push to you, not to Dependabot, and can cause
  Dependabot to "disavow" the PR afterward. Instead, use the server-side
  update-branch API: `gh api -X PUT repos/OWNER/REPO/pulls/NUMBER/update-branch`.
  This triggers a rebase attributed to the API call, not a human user.
- If Dependabot has already disavowed a PR (leaves a comment saying "this PR
  has been edited by someone other than Dependabot"), `@dependabot rebase` will
  not work. Use the same `PUT /update-branch` escape hatch -- it is not owned by
  Dependabot and works regardless of the disavowal state.
- If a Dependabot PR hits a rebase conflict because sibling PRs have merged
  changes to adjacent lines of the same manifest file, close it and open a
  manual replacement PR. Then add a `groups:` entry to [.github/dependabot.yml](.github/dependabot.yml)
  so the ecosystem cannot cascade-conflict again.
