# CLAUDE.md

This file is the discipline contract for AI-assisted development on this repository.
Every rule is mandatory. "Forbidden" means "do not do this under any circumstances
without stopping and asking the user first." Violations are bugs.

## Project Overview

Self-hosted PDF data extraction microservice. FastAPI backend, no database, no
frontend, no cloud runtime dependencies. The only external runtime dependency
is Ollama running locally on the host, serving the smallest Gemma 4 variant.

See the design and requirements specs at [`docs/superpowers/specs/`](docs/superpowers/specs/)
and the epic + feature graph tree at [`docs/graphs/PDFX/`](docs/graphs/PDFX/).

## Stack (do not deviate)

- Python 3.13, uv
- Backend: FastAPI, Pydantic v2, pydantic-settings, structlog, httpx
- Extraction: Docling (PDF parsing + OCR), LangExtract (orchestration),
  Ollama + smallest Gemma 4 variant (local LLM), PyMuPDF (annotation)
- Testing: pytest + pytest-asyncio, schemathesis (contract), import-linter
- Task runner: Taskfile. No Make.
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
   Every required gate (lint, types, arch, skills, tests, errors) is enumerated
   directly in the `check` task's `cmds:` list in `Taskfile.yml` — never reach a
   gate only via a sibling task (issue #215). A meta-test in
   `apps/backend/tests/unit/architecture/test_taskfile_check_hygiene.py` pins
   this invariant.

## Architecture

### Backend: Vertical Slices

```
app/core/               -- config, logging
app/api/                -- middleware, exception handler, health/ready, shared deps
app/exceptions/         -- DomainError hierarchy (base + _generated/)
app/shared/             -- feature-agnostic helpers
app/schemas/            -- ErrorBody, ErrorDetail, ErrorResponse (one class per file)
app/features/<feature>/ -- Self-contained vertical slice. The extraction feature is
                           built out during feature-dev per PDFX-E002 through E007.
```

### Layer Rules (mechanically enforced where possible)

- Features cannot import from other features.
- `shared/`, `core/`, and `schemas/` never import from `features/`.
- Within the extraction feature, the layer DAG is defined in the design spec
  Section 10 and enforced by the `import-linter` contracts in
  [`apps/backend/architecture/import-linter-contracts.ini`](apps/backend/architecture/import-linter-contracts.ini)
  (PDFX-E007-F004). The contracts are documented in
  [docs/architecture.md](docs/architecture.md#architectural-contracts-pdfx-e007-f004).
  Two enforcement layers work together: import-linter (static graph) and
  AST-scan tests in `test_dynamic_import_containment.py` (dynamic imports,
  cross-feature imports). Third-party containment contracts (C3-C6) use
  `source_modules = app` so the entire codebase is covered, not just the
  extraction feature.
- Third-party dependencies are contained to specific implementation files:
  - Docling only in `features/extraction/parsing/docling_document_parser.py`.
  - PyMuPDF (`fitz`) only in `features/extraction/annotation/pdf_annotator.py`
    (and optional password preflight in the parser file).
  - LangExtract only in `features/extraction/extraction/extraction_engine.py`
    and the community provider plugin registration file.
  - Ollama HTTP client only in `features/extraction/intelligence/ollama_gemma_provider.py`
    and `features/extraction/intelligence/ollama_health_probe.py`.

## Forbidden Patterns -- Backend

- Never use `print`. Use structlog.
- Never use `logging.getLogger`. Use structlog.
- Never use f-string log messages. Use structlog's key=value pairs:
  `logger.info("event_name", key=value)` not `logger.info(f"thing {value}")`.
- Never raise `HTTPException`. Raise a DomainError subclass.
- Never write a `try/except` that silently swallows errors. If you catch, re-raise or log.
- Never return `None` to signal "not found." Raise `NotFoundError` or a specific
  subclass.
- Never edit files in `exceptions/_generated/`. Edit `errors.yaml`, run `task errors:generate`.
- Never use `os.environ` or `os.getenv`. Use pydantic-settings.
- Never use `datetime.now()` without `tz=`. Use `datetime.now(UTC)`.
- Never use `datetime.utcnow()`.
- Never put business logic in route handlers. Handlers call one service method.
- Never write a sync `def` route handler. All handlers are `async def`.
- Never use `run_in_executor` to bridge blocking code without `asyncio.to_thread`
  justification — if the CPU-bound work needs to be offloaded, it must be a
  deliberate, documented choice (e.g. inside `DoclingDocumentParser.parse` to
  keep the event loop responsive).
- Never use global singletons, service locators, or DI container libraries. Use
  FastAPI `Depends()` factories.
- Never import services directly in handlers. Wire via `Depends()` factories.
- Never import from one feature into another feature. Features are independent.
- Never inherit schemas across entities. Each entity has its own schemas.
- Never import from `exceptions._generated` directly. Import from `exceptions`.
- Never use `# type: ignore` without a comment explaining why.

### Extraction-specific forbidden patterns (added in PDFX-E001-F005)

- Never bypass `StructuredOutputValidator` for any LLM-to-structured-output path.
- Never hardcode an Ollama model tag in source. Always read from `Settings.ollama_model`.
- Never raise a generic `ValueError` or `RuntimeError` from within the extraction
  pipeline. Raise a `DomainError` subclass generated from `errors.yaml`.
  Exception: value-object constructor invariants (`__post_init__` / `__init__`)
  may raise `ValueError` because they guard against programming errors (wrong
  arguments), not runtime pipeline failures.
- Never import Docling, LangExtract, PyMuPDF, or the Ollama HTTP client outside
  their designated containment files (the names of which are listed in the
  "Third-party dependencies are contained to specific implementation files"
  list under "Layer Rules" above).
- Never return a response shape that omits a field declared by the skill's
  `output_schema`. The "every declared field always present" invariant is
  load-bearing for API stability.

## Forbidden Patterns -- Cross-cutting

- Never add a top-level folder without updating this file and `docs/decisions.md`.
- Never write implementation before a failing test exists.
- Never commit without running `task check`.
- Never use `--no-verify`.
- Never add an env var without adding to both `Settings` and `apps/backend/.env.example`.
- Never add an error code without editing `errors.yaml` and running
  `task errors:generate`.
- Never skip a test level.
- Never introduce a new dependency without justification.
- Never write a test class. Use pytest functions.
- Never use `unittest.TestCase`. Use pytest.
- Never write a test with no assertions.

## Naming Conventions

- Python files: `snake_case.py`
- Python classes: `PascalCase` with role suffix (`ExtractionService`, `SkillLoader`)
- Python functions: `snake_case` verbs
- Python tests: `test_<unit>_<scenario>_<expected>`

## Error System

Source of truth: `packages/error-contracts/errors.yaml`
Generate: `task errors:generate` (produces `_generated/` Python files plus TypeScript and JSON outputs)

To add a new error:

1. Add code to `errors.yaml`.
2. Run `task errors:generate`.
3. Write a test that raises the error and asserts the response shape.

## Testing Rules

Three levels, all mandatory for every feature:

1. **Unit** -- no network, no Ollama, no real PDFs. Fast (<10 s for the whole suite).
2. **Integration** -- in-process against the FastAPI ASGI app via
   `httpx.AsyncClient`. Real Docling against fixture PDFs is allowed; Ollama is
   always mocked or stubbed via `Depends()` override. No DB, no external services.
3. **Contract** -- Schemathesis against the OpenAPI spec.

E2E with a real Ollama is optional-slow and excluded from the default `task check`
run. It lives under a `@pytest.mark.slow` marker.

Type checker (Pyright strict) is a build failure, not a warning.

Excluded: property-based, performance, mutation, snapshot, fuzz beyond Schemathesis.

## PR Authoring and Attribution

All human-authored pull requests on this repo are opened by
`cosminneamtiu02`. Dependabot-authored PRs remain the exception — they
are scoped under the Dependabot section below and their own auto-merge
workflow. This deliberately gives up Copilot auto-review for humans:
the `copilot_code_review` rule on the `main-protection` ruleset only
fires for PR authors with an active Copilot code-review seat scoped to
this repo, and cosmin's personal Copilot Pro does not extend to repos
he does not own. Reviews now come from human reviewers or explicit
reviewer assignment, not the ruleset. (Switched 2026-04-20 from an
earlier Ioana-as-author routing.)

### Before `gh pr create` on this repo

1. Run `gh api /user --jq .login` and verify the output is
   `cosminneamtiu02`. If it is anything else, run
   `gh auth switch --user cosminneamtiu02` and re-verify.
2. If the PR touches `.github/workflows/**` and you are pushing via
   `gh`/HTTPS using cosminneamtiu02's classic token, that token must
   carry the `workflow` scope or GitHub rejects the push server-side.
   SSH pushes and fine-grained-token pushes are governed differently
   (fine-grained tokens use permissions rather than scopes). One-time
   refresh for the classic-token `gh` flow:
   `gh auth refresh -s workflow -h github.com -u cosminneamtiu02`.
3. Do NOT switch mid-session on unrelated projects — `gh auth switch`
   flips the global gh identity for every shell on this machine. Commit
   to the switch for the duration of work on this repo.
4. VSCode's "GitHub" extension (Source Control sidebar, PR panel) uses a
   separate auth context from the gh CLI. If PR creation goes through
   the extension, sign out of it via VSCode → Accounts → Sign out and
   sign in as `cosminneamtiu02`. If PR creation goes through gh in the terminal,
   the VSCode extension's identity is irrelevant.

### Git author identity

Global git config pins the commit author to cosmin's verified no-reply email:

```
git config --global user.email "91669989+cosminneamtiu02@users.noreply.github.com"
git config --global user.name  "cosminneamtiu02"
```

User ID `91669989` was fetched from `/users/cosminneamtiu02`. The
`<user-id>+<login>@users.noreply.github.com` form is auto-provisioned by
GitHub for every account and never requires user action to verify —
required so GitHub resolves the commit author to the cosmin account.

`--global` is deliberate: it applies to every worktree on this machine
and every parallel Claude session, which is what we want for this
project. Cross-repo side effect: it also becomes the default identity
for commits on any other repo on this machine. If you also work on
repos where that's undesirable, override per-repo with
`git config user.email ...` (no `--global`) inside those repos.

Since cosmin is the direct PR author, no `Co-authored-by: cosminneamtiu02`
trailer is needed — GitHub credits the PR author directly.

Do NOT write manual `Co-authored-by:` trailers in commit messages or PR
body text on this repo. Long trailer lines get line-wrapped during the
PR-body-to-squash-commit transformation (observed on PR #20: an 89-char
trailer broke across two lines and became unparseable). The git-config
author path is robust; the manual trailer path is fragile.

That prohibition applies to human and AI attribution trailers alike —
do NOT use `Co-authored-by:` for Claude (or any other AI tool) either.
If you want AI-assistance transparency in the commit message or PR
body, write a plain non-trailer note such as `AI-assisted with Claude`
instead. Keeping the rule uniform avoids the "forbid-but-also-allow"
contradiction an earlier revision of this section carried, and keeps
all attribution off the fragile trailer path.

### Historical context

- **2026-04-14:** PR routing originally went through `ioanaecaterinastan-collab`
  to trigger Copilot auto-review (PR #19 validated — review in ~2 minutes;
  PRs #9–#17 as cosmin got no review). Cosmin's contribution credit on
  those Ioana-authored PRs flowed through an auto-extracted
  `Co-authored-by:` squash trailer from the branch-commit author field.
- **2026-04-20:** routing switched to cosmin as direct author. Copilot
  auto-review accepted as lost; no `Co-authored-by:` trailer needed.

## Dependabot

Close and delete any Dependabot PR that proposes a version older than latest.
Always use absolute latest versions for all dependencies.

**Auto-merge architecture** (see [docs/automerge.md](docs/automerge.md) for the full explainer):

- Dependabot-authored PRs that pass all required status checks are automatically
  squash-merged by [.github/workflows/dependabot-automerge.yml](.github/workflows/dependabot-automerge.yml).
  This is the ONE exception to the manual-Squash-button rule, documented in
  [docs/decisions.md ADR-010](docs/decisions.md).
- Never click merge on a green Dependabot PR. Let auto-merge handle it. If it's
  not auto-merging, something is wrong — fix the root cause rather than merging
  manually.
- Never auto-merge a non-Dependabot PR. The workflow's `if:` guard scopes the
  exception strictly via `github.event.pull_request.user.login == 'dependabot[bot]'`.
  Human PRs merge manually via the green Squash button, always.
- Never use `github.actor` in any auto-merge guard condition. It reads the event
  triggerer, not the PR author, and will silently skip the workflow whenever a
  human interacts with a Dependabot PR (e.g. clicks "Update branch"). Always read
  `github.event.pull_request.user.login`.
- Never set `DEPENDABOT_AUTOMERGE_ENABLED` to `"true"` until the `main-protection`
  ruleset exists AND has all required status checks configured.
  `gh pr merge --auto` waits only for the checks declared on the ruleset; with no
  ruleset, `--auto` has nothing to wait for and merges immediately including red
  PRs. This was incident PR #19 on 2026-04-12.
- Never bypass the ruleset. Never add anyone (including yourself) to the bypass
  list. Never disable the workflow with `--no-verify` or equivalent. If auto-merge
  is misbehaving, flip the variable to `"false"` (`gh variable set
  DEPENDABOT_AUTOMERGE_ENABLED --body "false"`) to disable it cleanly.

**Handling broken Dependabot PRs:**

- The template ships [.github/workflows/dependabot-lockfile-sync.yml](.github/workflows/dependabot-lockfile-sync.yml)
  which auto-fixes the uv lockfile-gap bug on Dependabot PRs once the repo
  variable `DEPENDABOT_LOCKFILE_SYNC_ENABLED` is set to `"true"` and the repo
  secret `DEPENDABOT_LOCKFILE_SYNC_PAT` contains a fine-grained PAT. With both
  in place, broken Dependabot PRs self-heal within ~2 minutes of opening. Never
  disable the sync workflow except via the `DEPENDABOT_LOCKFILE_SYNC_ENABLED`
  variable kill switch.
- If the sync workflow is not enabled OR has failed AND a Dependabot PR touches
  only `pyproject.toml` and not `uv.lock`, CI will reject it with a frozen-lockfile
  error. Do not try to fix the PR in place. Close it, run `uv lock` locally,
  commit manifest + lockfile atomically, open a replacement PR.
- Never use `GITHUB_TOKEN` to push lockfile fixes from a workflow. `GITHUB_TOKEN`-
  authored pushes do not trigger subsequent workflow runs, so CI will not re-run
  on the fixed commit and the PR will stay stuck. Always use a PAT (or a GitHub
  App installation token).
- If a Dependabot PR is `BEHIND` main (stale base), never click "Update branch"
  in the UI — it attributes the push to you, not to Dependabot, and can cause
  Dependabot to "disavow" the PR afterward. Instead, use the server-side
  update-branch API: `gh api -X PUT repos/OWNER/REPO/pulls/NUMBER/update-branch`.
- If Dependabot has already disavowed a PR, `@dependabot rebase` will not work.
  Use the same `PUT /update-branch` escape hatch.
- If a Dependabot PR hits a rebase conflict because sibling PRs have merged
  changes to adjacent lines of the same manifest file, close it and open a
  manual replacement PR. Then add a `groups:` entry to
  [.github/dependabot.yml](.github/dependabot.yml) so the ecosystem cannot
  cascade-conflict again.
