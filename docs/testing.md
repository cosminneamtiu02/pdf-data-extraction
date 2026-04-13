# Testing

## Philosophy

This is a test-driven development project. Every piece of functionality is written
test-first: red -> green -> refactor. New code without a test is a bug.

## Four Test Levels

All four levels are mandatory for every feature. Missing a level = incomplete work.

### 1. Unit Tests

- **What:** Individual functions, classes, pure logic in isolation.
- **Dependencies:** None. No database, no network, no file system, no containers.
- **Backend:** pytest + pytest-asyncio. Location: `tests/unit/` mirroring source tree.
- **Frontend:** Vitest + React Testing Library. Location: co-located `*.test.tsx`.
- **Speed budget:** Entire unit suite < 10 seconds locally.

### 2. Integration Tests

- **What:** Multiple components with real dependencies. Real Postgres, real HTTP, real service-to-repository calls.
- **Backend:** pytest + Testcontainers + httpx.AsyncClient. Session-scoped Postgres container. Transactional rollback per test. Location: `tests/integration/`.
- **Frontend:** Not a separate level. Covered by E2E + unit with MSW where needed.
- **Speed budget:** Entire integration suite < 2 minutes locally.
- **No SQLite.** SQLite lies about Postgres behavior. Banned.

### 3. End-to-End Tests

- **What:** Full system. Real browser, real frontend, real backend, real Postgres.
- **Tooling:** Playwright. Full docker-compose stack.
- **Location:** `tests/e2e/` in the frontend directory.
- **Scope:** Few tests, boring flows. Template ships one E2E test for Widget CRUD.
- **Speed budget:** Entire E2E suite < 3 minutes in CI.

### 4. Contract Tests

Two independent checks, both required:

1. **Schemathesis** reads the OpenAPI spec and fuzzes every endpoint. Asserts responses
   conform to declared schemas. Location: `tests/contract/test_schemathesis.py`.
2. **Generated client diff check.** CI runs `task client:generate` then
   `git diff --exit-code` on the generated TypeScript types. Non-empty diff = developer
   forgot to regenerate after an API change.

## Type-Driven Discipline

- **Backend:** Pyright strict. Enforced in CI. Type error = build failure.
- **Frontend:** `tsc --noEmit` with `strict: true`, `noUncheckedIndexedAccess: true`,
  `exactOptionalPropertyTypes: true`. Enforced in CI.

## Test Naming

| Context | Pattern | Example |
|---|---|---|
| Python | `test_<unit>_<scenario>_<expected>` | `test_widget_service_create_returns_widget_read` |
| Vitest | `describe("<Subject>") { it("<behavior>") }` | `describe("WidgetList") { it("renders empty state") }` |
| Playwright | `test("<user-facing behavior>")` | `test("user can create a widget and see it in the list")` |

## Test File Location

- Backend unit: `tests/unit/<mirrors source tree>/test_<module>.py`
- Backend integration: `tests/integration/<mirrors source tree>/test_<module>.py`
- Backend contract: `tests/contract/test_schemathesis.py`
- Frontend unit: co-located `<component>.test.tsx`
- Frontend E2E: `tests/e2e/<flow>.spec.ts`

## Pre-commit / Pre-push / CI

| Layer | What runs | Speed |
|---|---|---|
| Pre-commit | ruff, biome, whitespace, yaml/json check | ~10-15s |
| Pre-push | pytest unit + vitest | ~15s |
| CI | All four test levels + type checkers + Storybook build + generated file diffs | Full |

## Explicitly Excluded

- Property-based testing (Hypothesis, fast-check)
- Performance / load testing (Locust, k6)
- Mutation testing (mutmut, Stryker)
- Snapshot testing (forbidden -- snapshots rot)
- Fuzz testing beyond Schemathesis
