# Template Friction Log

This file tracks friction points discovered when using this template on real projects.
It is empty at template creation time and maintained on project #1 onward.

The purpose is to identify patterns that are:
- Too rigid for real-world use
- Missing from the template
- Incorrectly abstracted
- Over-engineered for  the actual use case

Template v2 will be extracted from real project usage, informed by this log.

## Friction Points

<!-- Add entries as: ### Date - Description -->
<!-- Example: ### 2026-05-01 - BaseRepository.list() doesn't support filtering -->

### 2026-04-12 — Dependabot doesn't regenerate pnpm workspace lockfile — ✅ FIXED at template level

When the monorepo uses a single root `pnpm-lock.yaml` with per-workspace `package.json` files, Dependabot updates only the manifest and silently fails to regenerate the lockfile. CI then rejects every Dependabot PR with `ERR_PNPM_OUTDATED_LOCKFILE` when `pnpm install --frozen-lockfile` runs. Observed on `@tanstack/react-query`, `@tanstack/react-router`, `@tanstack/router-devtools`, `@tanstack/router-plugin` — four individual PRs that all had the same failure mode.

**Template-level workaround we shipped first:** aggressive `groups:` in [.github/dependabot.yml](.github/dependabot.yml) so at least the N broken PRs become 1 broken PR instead of N. Also a runbook entry in [docs/automerge.md](docs/automerge.md) for the close-and-replace manual workflow.

**Template-level fix we shipped next:** [.github/workflows/dependabot-lockfile-sync.yml](.github/workflows/dependabot-lockfile-sync.yml) — a new workflow that fires on every Dependabot PR, detects whether the PR modified `package.json` or `pyproject.toml` without the corresponding lockfile, runs the package manager in regeneration mode, and pushes the updated lockfile back to the PR branch as a follow-up commit. Composes naturally with the existing auto-merge workflow: sync fires → pushes lockfile fix → new `synchronize` event → auto-merge workflow re-fires (harmless idempotent) and CI re-runs on the fixed commit → checks pass → auto-merge queue executes the squash-merge. Net result: a Dependabot PR with the lockfile-gap bug goes from "stuck red indefinitely" to "auto-merges cleanly" within about 2–3 minutes, with zero human intervention.

**Prerequisites for the sync workflow to function** (downstream project concern — documented in [docs/new-project-setup.md Phase 5b](docs/new-project-setup.md)):
1. A fine-grained PAT with `Contents: Read and write` + `Pull requests: Read and write` scoped to the project's one repo. Must be a PAT, not `GITHUB_TOKEN`, because `GITHUB_TOKEN`-authored pushes do not trigger subsequent workflow runs and CI would never re-run on the fixed commit.
2. Repo secret `DEPENDABOT_LOCKFILE_SYNC_PAT` set to that PAT.
3. Repo variable `DEPENDABOT_LOCKFILE_SYNC_ENABLED` set to `"true"`.

Both the variable and secret are unset on the template itself so the workflow is dormant. Downstream projects enable them after their Phase 4 ruleset is in place.

**Backend equivalent:** `uv sync --dev` is lenient about `pyproject.toml` / `uv.lock` divergence, so backend Dependabot PRs don't surface the bug as loudly, but `uv.lock` in git is silently out of sync with the manifest after each merge. The new lockfile-sync workflow **also** handles backend: it runs `uv lock` in `apps/backend` and `packages/error-contracts` when those manifests changed, committing the regenerated lockfile back. This means `uv.lock` stays authoritative in git without needing to switch CI to `uv sync --frozen`.

**Upstream bug status:** still unfixed in `dependabot-core`. The workaround workflow is tool-side infrastructure — the moment dependabot-core ships a proper fix for pnpm workspaces, the sync workflow becomes redundant and can be deleted. Until then, it's load-bearing.

### 2026-04-12 — `github.actor` is the wrong field for Dependabot auto-merge workflows

The template's initial auto-merge workflow pattern (which we shipped in the first iteration of [.github/workflows/dependabot-automerge.yml](.github/workflows/dependabot-automerge.yml)) used `github.actor == 'dependabot[bot]'` as the scope guard. `github.actor` reads the current event's triggerer, not the PR author. When a human clicks "Update branch" on a Dependabot PR, the resulting `synchronize` event's actor is the human, and the guard evaluates false. All five backend Dependabot PRs were stuck in this state for ~10 minutes until the guard was fixed.

**Template-level fix we shipped:** hotfix changed the guard to `github.event.pull_request.user.login == 'dependabot[bot]'`, which reads the PR author from the event payload. That field stays `dependabot[bot]` for the lifetime of the PR regardless of who triggers individual events. Inline workflow comment + ADR-010 guard-condition paragraph + CLAUDE.md Dependabot section + [docs/automerge.md Incident 2](docs/automerge.md) all explicitly document the correct pattern so it cannot be silently reintroduced.

**Why the trap is so common:** GitHub's own auto-merge docs recommended `github.actor` until late 2023. The wrong pattern propagated to hundreds of public workflow examples. Any future edit that "simplifies" the guard based on a search result will reintroduce the bug.

### 2026-04-12 — Auto-merge without a ruleset silently merges red PRs

The template's auto-merge workflow called `gh pr merge --auto --squash` directly, trusting that GitHub's merge queue would wait for required status checks before actually merging. This is true **if and only if a ruleset with required status checks exists**. With no ruleset, `--auto` has nothing to wait for and merges immediately, including PRs with failing CI.

Observed on PR #19 (the first grouped TanStack Dependabot PR after the grouping config landed): it merged on the spot with `frontend-checks` and `api-client-checks` red because the workflow was deployed before the `main-protection` ruleset was created. Main was broken for ~2 minutes until a follow-up PR with the lockfile fix race-landed and accidentally repaired it.

**Template-level fix we shipped:** hotfix added a `DEPENDABOT_AUTOMERGE_ENABLED` repo variable. The workflow's `if:` guard now requires the variable to be literally `"true"` before running. The user must set the variable only after verifying the ruleset exists with all required status checks — documented as a hard prerequisite in [new-project-setup.md Phase 5a](docs/new-project-setup.md). The variable also serves as the emergency kill switch (`gh variable set DEPENDABOT_AUTOMERGE_ENABLED --body "false"`).

**Root cause of the original assumption:** conflated `allow_auto_merge` (a repo setting controlling the UI button) with "the thing that gates --auto". They are not the same. `--auto` is gated by the *ruleset's* required status checks, not the repo setting.

### 2026-04-12 — UI "Update branch" button causes Dependabot to disavow PRs

When a human clicks "Update branch" on a Dependabot PR, GitHub performs the rebase and attributes the resulting push to the human. Dependabot's safety policy then marks the PR as "edited by someone other than Dependabot" and refuses to run `@dependabot rebase` / `@dependabot recreate` commands on it thereafter.

Observed on all 5 backend Dependabot PRs during the auto-merge setup session. Once disavowed, there was no Dependabot-controlled path to unstick them.

**Template-level workaround we found:** use the server-side `PUT /repos/OWNER/REPO/pulls/NUMBER/update-branch` API endpoint instead of the UI button. This endpoint is not owned by Dependabot, works regardless of disavowal state, and respects the repo's configured merge method (squash-only in our case). Documented in [CLAUDE.md](CLAUDE.md), [docs/automerge.md](docs/automerge.md), and [new-project-setup.md](docs/new-project-setup.md).

**Template-level fix still needed:** GitHub's UI "Update branch" button should call this same server-side endpoint when clicked on a Dependabot PR, so disavowal doesn't happen. That's a GitHub product decision, not something we can fix at the template level. Document avoidance only.

### 2026-04-12 — Adjacent manifest line edits produce rebase-conflict cascades

When multiple Dependabot PRs modify adjacent lines in the same manifest file (e.g. `pyproject.toml`'s dependency list), merging them sequentially produces 3-way merge conflicts on the later PRs. The conflicts are contextual, not semantic — each PR's one-line edit would apply cleanly if the surrounding context hadn't drifted. Observed on PR #16 (`asyncpg`) after PRs #12–#15 merged bumps to `testcontainers`, `sqlalchemy`, `alembic`, `schemathesis`.

**Template-level fix we shipped:** aggressive `groups:` in [.github/dependabot.yml](.github/dependabot.yml) for every ecosystem where interlocking dependencies touch the same manifest file. Specifically: `sqlalchemy-stack` (sqlalchemy + alembic + asyncpg), `fastapi-stack`, `pydantic`, `pytest`, `tanstack`, `react`, `storybook`, `vitest`, `testing-library`, `tailwind`, `i18next`, `dinero`.

**Takeaway codified:** when you see a fifth Dependabot PR for the same ecosystem, it's almost always doomed to cascade-conflict. Add a group and don't merge siblings individually.
