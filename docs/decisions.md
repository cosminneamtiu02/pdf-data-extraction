# Architectural Decision Record

Decisions that shape this template. Each entry is final unless explicitly superseded.

## ADR-001: Vertical Slices over Layered-by-Role

**Status:** Accepted
**Date:** 2026-04-07

Backend and frontend use vertical feature slices. Each feature is a self-contained folder
(`features/<name>/`) with all its layers inside. Shared abstractions live outside features
in `shared/`, `core/`, `schemas/`, `types/`.

**Rationale:** Scales to production. A senior dev sees domain boundaries immediately.
AI-assisted development benefits from one-folder-per-feature context. Adding or removing
a feature touches one folder.

**Rejected:** Layered-by-role (Django/Rails style). Doesn't scale past ~15 entities.
Related code scattered across 5+ folders.

## ADR-002: Offset Pagination over Cursor

**Status:** Accepted
**Date:** 2026-04-07

List endpoints use offset-based pagination (`page`, `size`). Response shape: `Page[T]`
with `items`, `total`, `page`, `size`, `pages`.

**Rationale:** Simple. Supports both "load more" and "page 1, 2, 3" UI patterns. Performance
cliff only at tens of thousands of rows with high offsets. Acceptable for template and early
production. Convert specific endpoints to cursor pagination when needed.

**Rejected:** Cursor-based. Adds complexity without solving a problem at template scale.
Can't "jump to page 5."

## ADR-003: Generated Error Contracts

**Status:** Accepted
**Date:** 2026-04-07

All errors crossing the API boundary are defined in `packages/error-contracts/errors.yaml`.
A codegen script produces typed Python exception classes and TypeScript types. A validator
ensures translations in every locale match the contract.

**Rationale:** One source of truth. Type safety end-to-end. Missing translation = build error.
Boring to extend (edit YAML, run codegen, add translations).

**Rejected:** Hand-written error classes without codegen (drift risk). Flat untyped error
codes without parameter contracts (no type safety).

## ADR-004: One Class Per File

**Status:** Accepted
**Date:** 2026-04-07

Every Python class and every React component lives in its own file. No exceptions except
generated code in `_generated/` directories (one file per generated class).

**Rationale:** Grep-ability. AI-friendly. Prevents file bloat. Forces explicit imports.

## ADR-005: Health at Root, Business at /api/v1/

**Status:** Accepted
**Date:** 2026-04-07

`/health` and `/ready` live at the root, outside `/api/v1/`. Business endpoints live under
`/api/v1/`.

**Rationale:** Load balancers and K8s probes hardcode health paths. Versioning health
endpoints forces infrastructure config changes on API version bumps.

## ADR-006: i18n from Day One

**Status:** Accepted
**Date:** 2026-04-07

i18next with `/$lang/` URL prefix. Detection: localStorage -> navigator.languages -> default
`en`. Supported: `en`, `ro`. Error codes are i18n lookup keys.

**Rationale:** Retrofitting i18n means auditing every component for raw strings. The library
is trivial; the discipline is months of work to add later.

## ADR-007: Money as Value Object

**Status:** Accepted
**Date:** 2026-04-07

Backend `Money` type: `amount` (int, minor units) + `currency` (ISO 4217 string). Frontend
uses dinero.js. No floats for monetary values, ever.

**Rationale:** Float bugs corrupt data silently. The cost of shipping Money and never using
it is ~100 lines. The cost of not shipping it is auditing every monetary calculation.

## ADR-008: Biome + ESLint Dual Setup

**Status:** Accepted
**Date:** 2026-04-07

Biome is the primary linter/formatter. ESLint runs alongside solely for the
`i18next/no-literal-string` rule. ESLint config is minimal: one plugin, one rule.

**Rationale:** Biome lacks a native i18next literal string rule. The dual setup is the
least-bad option. Biome handles everything else.

## ADR-009: Pre-commit Fast, Pre-push Unit Tests, CI Everything

**Status:** Accepted
**Date:** 2026-04-07

Pre-commit: ruff, biome, trailing-whitespace, check-yaml/json (~10-15s).
Pre-push: pytest unit + vitest (~15s).
CI: all four test levels + type checkers + Storybook build + generated file diff checks.

**Rationale:** Fast commit loop. Tests before code leaves the machine. Full verification
before merge.

## ADR-010: Dependabot Auto-Merge Exception to Manual-Squash Rule

**Status:** Accepted
**Date:** 2026-04-12

The template's Phase 3 rule ([docs/new-project-setup.md](new-project-setup.md) line 47)
reads: *"Every merge in this repo uses the green 'Squash and merge' button. Always. No
exceptions."* This project extends that rule with exactly one exception: Dependabot PRs
that arrive green may be auto-merged by a workflow. Every human or source-code PR still
merges exclusively via the manual Squash button.

The mechanism is [.github/workflows/dependabot-automerge.yml](../.github/workflows/dependabot-automerge.yml),
which runs on every `pull_request` event, short-circuits unless the PR's author is
`dependabot[bot]`, and calls `gh pr merge --auto --squash` on the remaining PRs.
GitHub's native auto-merge queue then merges each such PR if and only if every
required status check on the `main-protection` ruleset is green and every conversation
is resolved — the exact same gates a human faces when clicking the button. The workflow
does not bypass any rule; it only presses the button on the project's behalf after the
ruleset has already approved.

**Guard condition — the PR author, not `github.actor`.** The workflow's `if:` reads
`github.event.pull_request.user.login`, not `github.actor`. `github.actor` is whoever
triggered the current event — when a human clicks "Update branch" in the UI on a
Dependabot PR, `github.actor` becomes that human and a condition based on it would
skip the job on every human-triggered sync, even though the PR is still owned by
Dependabot. The PR author from the event payload stays `dependabot[bot]` for the
lifetime of the PR regardless of which individual event is being processed, so it is
the correct field to scope the exception on. An earlier revision of this workflow
used `github.actor` and silently skipped all 5 open backend Dependabot PRs when they
were manually rebased via "Update branch"; the fix is recorded here so the pattern
is not reintroduced.

**Safety precondition — the ruleset is load-bearing.** `gh pr merge --auto` waits only
for the checks the ruleset declares required. If no ruleset exists, or the ruleset has
no required status checks, `--auto` has nothing to wait for and merges immediately
regardless of CI state — including merging a PR with failing checks. This is not a
theoretical concern: it happened on PR #19 (the first grouped-TanStack Dependabot PR),
which was auto-merged while `frontend-checks` and `api-client-checks` were red, because
`main-protection` did not yet exist. Main was accidentally in a broken state for about
two minutes until a follow-up PR with the lockfile fix landed. To prevent recurrence,
the workflow is gated on the `DEPENDABOT_AUTOMERGE_ENABLED` repo variable. The variable
must be set to `"true"` only after the `main-protection` ruleset has been created with
all required status checks. Until then, the workflow's `if:` evaluates false and the
job is a no-op, even for Dependabot PRs. Enabling the variable is a one-line action
(`gh variable set DEPENDABOT_AUTOMERGE_ENABLED --body "true"`) that must not be taken
before the ruleset is verified.

**Rationale:** The invariant the project cares about is "main is always green", not
"a human physically clicked the button". Dependabot PRs are the highest-volume,
lowest-novelty PRs in the system: one package bump, no source logic change, validated
by the same four (eventually five) required checks every other PR faces. Requiring a
human to manually squash each of them adds latency without adding safety — the safety
already lives in the ruleset. Automating the click lets the project absorb weekly
dependency updates without accumulating a backlog of green-but-unclicked PRs, which
is the failure mode that breaks the "always green" invariant in practice: stale green
PRs rebase-conflict and go red, not the other way around.

The exception is deliberately narrow. It is scoped by `github.actor` to Dependabot
alone, so no human PR, no code change, and no manually-triggered bot can ever take
this path. If Dependabot is compromised or misbehaves, the ruleset's required checks
are the containment — the same containment a manual clicker would rely on. There is
no scenario where this ADR relaxes the gates; it only automates the door past them.

**Rejected alternative 1:** Keep the manual-click rule absolute and let Dependabot PRs
queue until a human squashes them. Produces green-PR backlog, rebase churn, and
eventually red PRs from conflicts. Contradicts the "always green" invariant the rule
was meant to serve.

**Rejected alternative 2:** Expand auto-merge to all green PRs including human ones.
Removes human review of code changes entirely. The ruleset is a safety net, not a
review process — it catches regressions but not design mistakes, security smells in
new code, or domain-logic errors no test exercises. Auto-merging human PRs would
conflate the two.
