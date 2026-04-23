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
- **Backend:** `tests/contract/` contains three test files, each covering
  a distinct slice of the contract:
  - `test_schemathesis.py` — schemathesis conformance: loads the live
    OpenAPI spec via `schemathesis.openapi.from_asgi("/openapi.json", app)`
    and calls `schema["/api/v1/extract"]["POST"].validate_response(...)`
    on hand-rolled requests covering each declared status code (200,
    400, 404, 413, 422, 502, 503, 504) — some codes have multiple
    requests, one per distinct `DomainError` that maps to that code
    (e.g. 400 covers both `PdfInvalidError` and
    `PdfPasswordProtectedError`). Targeted requests rather than
    `@schema.parametrize` because schemathesis cannot synthesize valid
    PDFs from `format: binary`.
  - `test_extract_contract.py` — shape assertions for `/api/v1/extract`:
    verifies the OpenAPI spec declares the expected form fields and
    media types (`application/json`, `application/pdf`, `multipart/mixed`
    for the 200 path) and asserts `ErrorResponse` envelope shape for the
    413 (`PDF_TOO_LARGE`) and 504 (`INTELLIGENCE_TIMEOUT`) error responses.
  - `test_degraded_contract.py` — degraded-mode response shape
    conformance: pins `app.state.ollama_health_probe` to a `FakeProbe`
    so the startup probe fails deterministically, then asserts `/ready`
    returns 503 with the `{status: "not_ready", reason: <enum>}` shape
    declared in OpenAPI, and `/health` remains 200 (liveness is
    unaffected by Ollama reachability).

  Shared fixtures (valid skill YAML writer, `Settings` factory with
  `app_env="development"` so `/openapi.json` is exposed, and the canned
  `ExtractionResult`) live in `tests/contract/_helpers.py`.

### Optional — E2E (slow)

- **What:** End-to-end smoke test with a real Ollama + real Gemma 4 model
  against a fixture PDF. The slow bucket also covers real Docling parsing
  against fixture PDFs; the live-Ollama smoke test lives at
  `tests/integration/features/extraction/test_live_ollama_e2e.py` and
  POSTs through the full `/api/v1/extract` endpoint so the entire
  extraction pipeline (Docling → LangExtract → Ollama/Gemma → response
  assembly) is exercised in a single request.
- **Tooling:** Pytest with `@pytest.mark.slow`, excluded from default
  `task check`. Runnable via `task test:slow`.
- **Skip behaviour:** The live-Ollama E2E test skips cleanly when Ollama
  is not reachable at the configured `OLLAMA_BASE_URL` or the configured
  `OLLAMA_MODEL` tag is not installed — so `task test:slow` never fails
  loudly on machines without Ollama. To run it locally:
  `ollama serve &` then `ollama pull <OLLAMA_MODEL>` (e.g. `gemma4:e2b`),
  and if Ollama is on localhost rather than the docker-default host,
  export `OLLAMA_BASE_URL=http://localhost:11434` before invoking
  `task test:slow`.
- **Scope:** One live-Ollama test. Determinism is not required — the
  test asserts response shape and the "every declared field always
  present" invariant rather than specific Gemma output, so it catches
  catastrophic regressions in the Ollama integration without being
  flaky on model drift.

## Type-Driven Discipline

- **Backend:** Pyright strict. Enforced in CI. Type error = build failure.

## Test Naming

| Context | Pattern | Example |
|---|---|---|
| Python | `test_<unit>_<scenario>_<expected>` | `test_skill_loader_rejects_duplicate_versions` |

## Test File Location

- Backend unit: `tests/unit/<mirrors source tree>/test_<module>.py`
- Backend integration: `tests/integration/<mirrors source tree>/test_<module>.py`
- Backend contract: `tests/contract/` — flat directory, one file per
  contract slice (`test_schemathesis.py`, `test_extract_contract.py`,
  `test_degraded_contract.py`). See Section 3 above for the per-file
  role split. New contract tests land in the file whose role they
  match; add a new file if the slice is genuinely new.
- Infra hygiene: `infra/tests/hygiene/` — static assertions about files
  outside `apps/backend/` (Dockerfile, GitHub Actions workflows,
  Dependabot config). Run via `task check:hygiene`, which is wired
  into `task check` as a direct gate. Relocated here per issue #400
  so the backend unit-test tree no longer carries targets outside
  its own scope.

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
