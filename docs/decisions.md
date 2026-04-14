# Architectural Decision Record

Decisions that shape this project. Each entry is final unless explicitly superseded.

## ADR-001: Vertical Slices over Layered-by-Role

**Status:** Accepted
**Date:** 2026-04-07

The backend uses vertical feature slices. Each feature is a self-contained
folder (`features/<name>/`) with all its layers inside. Shared abstractions live
outside features in `shared/`, `core/`, `schemas/`.

**Rationale:** Scales to production. A senior dev sees domain boundaries
immediately. AI-assisted development benefits from one-folder-per-feature
context. Adding or removing a feature touches one folder.

**Rejected:** Layered-by-role (Django/Rails style). Doesn't scale past
~15 entities. Related code scattered across 5+ folders.

## ADR-003: Generated Error Contracts

**Status:** Accepted
**Date:** 2026-04-07

All errors crossing the API boundary are defined in
`packages/error-contracts/errors.yaml`. A codegen script produces typed Python
exception classes.

**Rationale:** One source of truth. Type safety end-to-end. Boring to extend
(edit YAML, run codegen).

**Rejected:** Hand-written error classes without codegen (drift risk). Flat
untyped error codes without parameter contracts (no type safety).

## ADR-004: One Class Per File

**Status:** Accepted
**Date:** 2026-04-07

Every Python class lives in its own file. No exceptions except generated code in
`_generated/` directories (one file per generated class anyway).

**Rationale:** Grep-ability. AI-friendly. Prevents file bloat. Forces explicit
imports.

## ADR-005: Health at Root, Business at /api/v1/

**Status:** Accepted
**Date:** 2026-04-07

`/health` and `/ready` live at the root, outside `/api/v1/`. Business endpoints
live under `/api/v1/`.

**Rationale:** Orchestrators and load balancers hardcode health paths.
Versioning health endpoints forces infrastructure config changes on API version
bumps.

## ADR-009: Pre-commit Fast, Pre-push Unit Tests, CI Everything

**Status:** Accepted
**Date:** 2026-04-07

Pre-commit: ruff, whitespace, yaml/json check (~5 s).
Pre-push: pytest unit (~10 s).
CI: all test levels + type checker + architecture contracts + error contracts.

**Rationale:** Fast commit loop. Tests before code leaves the machine. Full
verification before merge.

## ADR-010: Dependabot Auto-Merge Exception to Manual-Squash Rule

**Status:** Accepted
**Date:** 2026-04-12

The template's Phase 3 rule
([docs/new-project-setup.md](new-project-setup.md)) reads: *"Every merge in this
repo uses the green 'Squash and merge' button. Always. No exceptions."* This
project extends that rule with exactly one exception: Dependabot PRs that
arrive green may be auto-merged by a workflow. Every human or source-code PR
still merges exclusively via the manual Squash button.

The mechanism is
[.github/workflows/dependabot-automerge.yml](../.github/workflows/dependabot-automerge.yml),
which runs on every `pull_request` event, short-circuits unless the PR's author
is `dependabot[bot]`, and calls `gh pr merge --auto --squash` on the remaining
PRs. GitHub's native auto-merge queue then merges each such PR if and only if
every required status check on the `main-protection` ruleset is green and every
conversation is resolved — the exact same gates a human faces when clicking the
button. The workflow does not bypass any rule; it only presses the button on
the project's behalf after the ruleset has already approved.

**Guard condition — the PR author, not `github.actor`.** The workflow's `if:`
reads `github.event.pull_request.user.login`, not `github.actor`. `github.actor`
is whoever triggered the current event — when a human clicks "Update branch"
in the UI on a Dependabot PR, `github.actor` becomes that human and a condition
based on it would skip the job on every human-triggered sync, even though the
PR is still owned by Dependabot.

**Safety precondition — the ruleset is load-bearing.** `gh pr merge --auto`
waits only for the checks the ruleset declares required. If no ruleset exists,
or the ruleset has no required status checks, `--auto` has nothing to wait for
and merges immediately regardless of CI state. The workflow is gated on the
`DEPENDABOT_AUTOMERGE_ENABLED` repo variable, which must be set to `"true"` only
after the `main-protection` ruleset has been created with all required status
checks.

**Rationale:** The invariant the project cares about is "main is always green",
not "a human physically clicked the button". Dependabot PRs are the highest-
volume, lowest-novelty PRs in the system: one package bump, no source logic
change, validated by the same required checks every other PR faces. Requiring
a human to manually squash each of them adds latency without adding safety —
the safety already lives in the ruleset. Automating the click lets the project
absorb weekly dependency updates without accumulating a backlog of green-but-
unclicked PRs.

The exception is deliberately narrow. It is scoped to Dependabot alone, so no
human PR, no code change, and no manually-triggered bot can ever take this
path. If Dependabot is compromised or misbehaves, the ruleset's required checks
are the containment.

## ADR-011: Template-to-Project Bootstrap (2026-04-13)

**Status:** Accepted
**Date:** 2026-04-13

The project started as a full-stack monorepo template (React frontend, FastAPI
backend, Postgres database, Alembic migrations, widget CRUD reference slice,
i18n, Storybook, Terraform scaffold). A one-shot `project-bootstrap` skill run
stripped the template down to match the PDF extraction microservice's actual
needs: no frontend, no database, no Alembic, no widget slice, no Money type,
no i18n, no Terraform, no Storybook, no `api-client` package, no JS workspace
shell. The design and requirements specs at
[`docs/superpowers/specs/`](superpowers/specs/) and the graph tree at
[`docs/graphs/PDFX/`](graphs/PDFX/) drive feature-dev from here.

**Rationale:** Running AI-assisted development on a bloated baseline wastes
context and creates false constraints. A lean starting point matches what the
project actually does.

**Rejected alternatives:**

- Keeping the full template and implementing the extraction feature as one
  more vertical slice alongside widget CRUD. Rejected because CLAUDE.md's
  forbidden-patterns section would have been internally inconsistent (some
  rules apply to the DB layer that no longer exists in the extraction feature,
  and updating them piecemeal would have left stale guidance in place).
- Copying the template to a new repo and stripping there. Rejected because the
  project started with an existing git history we wanted to preserve.

## Superseded ADRs

- **ADR-002 (offset pagination)** — superseded. There are no paginated
  endpoints; the extraction service has one endpoint, and its response is not
  a list.
- **ADR-006 (i18n from day one)** — superseded. The service is API-only with
  no human-facing strings. Error responses are machine-readable codes, not
  localized messages.
- **ADR-007 (Money as value object)** — superseded. No monetary handling.
- **ADR-008 (Biome + ESLint dual setup)** — superseded. No frontend.
