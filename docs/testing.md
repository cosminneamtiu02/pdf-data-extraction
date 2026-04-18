# Testing

## Philosophy

This is a test-driven development project. Every piece of functionality is written
test-first: red -> green -> refactor. New code without a test is a bug.

## Three Test Levels

Three levels, all mandatory for every feature. E2E is optional-slow.

### 1. Unit Tests

- **What:** Individual functions, classes, pure logic in isolation.
- **Dependencies:** None. No network, no Ollama, no real PDFs.
- **Backend:** pytest + pytest-asyncio. Location: `tests/unit/` mirroring source tree.
- **Speed budget:** Entire unit suite < 10 seconds locally.

### 2. Integration Tests

- **What:** Multiple components in-process against the FastAPI ASGI app.
- **Backend:** pytest + `httpx.AsyncClient` with `ASGITransport`. No external
  services. Real Docling against fixture PDFs is allowed. Ollama is stubbed via
  `Depends()` override with a mock `IntelligenceProvider`.
- **Speed budget:** Entire integration suite < 30 seconds locally for the post-
  bootstrap shell; more once the extraction pipeline lands and fixture PDFs are
  exercised.

### 3. Contract Tests

- **What:** Validates the generated OpenAPI spec shape and exercises
  `/api/v1/extract` with schemathesis-driven response validation across
  every declared status code (200, 400, 404, 413, 422, 502, 503, 504).
  Heavy pipeline components (Docling, LangExtract, Ollama, PyMuPDF) are
  stubbed via `app.dependency_overrides` so contract tests remain fast
  and deterministic.
- **Backend:** `tests/contract/test_schemathesis.py`.

### Optional — E2E (slow)

- **What:** End-to-end smoke test with a real Ollama + real Gemma 4 model
  against a fixture PDF.
- **Tooling:** Pytest with `@pytest.mark.slow`, excluded from default
  `task check`. Runnable via `task test:slow` after the marker is added in
  feature-dev.
- **Scope:** One test only. Determinism is not required — the test catches
  catastrophic regressions in the Ollama integration.

## Type-Driven Discipline

- **Backend:** Pyright strict. Enforced in CI. Type error = build failure.

## Test Naming

| Context | Pattern | Example |
|---|---|---|
| Python | `test_<unit>_<scenario>_<expected>` | `test_skill_loader_rejects_duplicate_versions` |

## Test File Location

- Backend unit: `tests/unit/<mirrors source tree>/test_<module>.py`
- Backend integration: `tests/integration/<mirrors source tree>/test_<module>.py`
- Backend contract: `tests/contract/test_schemathesis.py`

## Pre-commit / Pre-push / CI

| Layer | What runs | Speed |
|---|---|---|
| Pre-commit | ruff, whitespace, yaml/json check | ~5 s |
| Pre-push | pytest unit | ~10 s |
| CI | unit + integration + contract + type checker + import-linter + error contracts | Full |

## Explicitly Excluded

- Property-based testing (Hypothesis, fast-check)
- Performance / load testing (Locust, k6)
- Mutation testing (mutmut, Stryker)
- Snapshot testing (forbidden -- snapshots rot)
- Fuzz testing beyond Schemathesis
