# Dependabot Auto-Merge Architecture

How the "Dependabot PRs auto-merge if and only if everything is green" invariant is actually enforced. Read this end-to-end the first time. Bookmark it as a reference the next time something misbehaves.

## TL;DR

Dependabot-authored PRs that pass all required status checks are automatically squash-merged. Human-authored PRs always merge manually via the Squash button. The mechanism is a **paired contract** between three things that must all exist and all agree:

| Component | File / Setting | What it does |
|---|---|---|
| Auto-merge workflow | [.github/workflows/dependabot-automerge.yml](../.github/workflows/dependabot-automerge.yml) | **Who** gets auto-merged (scoped by PR author + a repo variable) |
| Lockfile sync workflow | [.github/workflows/dependabot-lockfile-sync.yml](../.github/workflows/dependabot-lockfile-sync.yml) | **Fixes** Dependabot's lockfile-gap bug so the auto-merge pipeline can succeed |
| Ruleset | `main-protection` (Settings → Rules → Rulesets) | **What** has to be green before any merge |
| Auto-merge variable | `DEPENDABOT_AUTOMERGE_ENABLED` (Settings → Actions → Variables) | **Whether** the auto-merge workflow is armed |
| Lockfile sync variable | `DEPENDABOT_LOCKFILE_SYNC_ENABLED` (Settings → Actions → Variables) | **Whether** the sync workflow is armed |
| Lockfile sync PAT | `DEPENDABOT_LOCKFILE_SYNC_PAT` (Settings → Actions → Secrets) | **Authentication** the sync workflow uses to push lockfile fixes back (must be a PAT, not `GITHUB_TOKEN`) |

If any single one of these is missing, misconfigured, or out of sync with the others, the invariant breaks. This document explains each, explains how they compose, and walks through the three historical incidents that taught us why every piece is necessary.

---

## The three components

### 1. Workflow — [.github/workflows/dependabot-automerge.yml](../.github/workflows/dependabot-automerge.yml)

Triggers on every `pull_request` event (`opened`, `synchronize`, `reopened`). Contains a single job with a two-clause `if:` guard:

```yaml
if: github.event.pull_request.user.login == 'dependabot[bot]'
    && vars.DEPENDABOT_AUTOMERGE_ENABLED == 'true'
```

If the guard returns true, the job runs one step:

```yaml
- run: gh pr merge --auto --squash "$PR_URL"
```

If the guard returns false, the job reports `conclusion: skipped` in the Actions tab and exits with exit code 0. No error. Human PRs take this path always.

**What `gh pr merge --auto --squash` actually does.** It does NOT merge the PR immediately. It sets the PR's `autoMergeRequest` field, telling GitHub's native merge queue: "merge this PR using the squash method as soon as it satisfies every required gate." GitHub then waits. When every required status check on `main-protection` is green, every unresolved conversation is resolved, and the branch is up to date, GitHub's queue executes the squash-merge without any further intervention.

**Why the guard reads `pull_request.user.login`, not `github.actor`.** `github.actor` is the entity that triggered the **current event**. When a human clicks "Update branch" on a Dependabot PR — which is a common action when the strict "branches up to date" rule kicks in — the resulting `synchronize` event's actor is the human, not the PR author. A guard that checks `github.actor == 'dependabot[bot]'` would skip in that case, even though the PR is still authored and managed by Dependabot. `pull_request.user.login` reads the PR author from the event payload and stays `dependabot[bot]` for the lifetime of the PR regardless of who fires individual events on it.

This distinction is the subject of [ADR-010's guard-condition paragraph](decisions.md#adr-010-dependabot-auto-merge-exception-to-manual-squash-rule). GitHub's own Dependabot auto-merge docs used the wrong pattern (`github.actor`) for about a year before being corrected; the wrong pattern remains widespread in public workflow examples.

### 2. Ruleset — `main-protection`

Lives at Settings → Rules → Rulesets → `main-protection`. Active. Targets the default branch. Bypass list is empty (even for admins). Enforces:

- **Restrict deletions** — cannot delete `main`.
- **Require linear history** — no merge commits on main. Compatible with squash-only merging.
- **Require a pull request before merging** — no direct pushes. Sub-rules:
  - 0 approvals required (solo dev pattern; otherwise Dependabot can't merge)
  - Dismiss stale pull request approvals when new commits are pushed
  - Require conversation resolution before merging
  - Allowed merge methods: squash only
- **Require status checks to pass** — with `strict: true` (branches must be up to date). Required contexts:
  - `backend-checks`
  - `frontend-checks`
  - `api-client-checks`
  - `error-contracts`
- **Block force pushes** — cannot rewrite history on main.

The ruleset is load-bearing for the whole auto-merge system. Specifically, the **required status checks** list is what `gh pr merge --auto` waits for. With an empty or missing ruleset, `--auto` has nothing to wait for and merges immediately — including merging PRs with failing CI. This is not theoretical; it happened once (see the Incident Log below).

**Why `integration_id: 15368` matters in the ruleset response.** When you create the ruleset and add the four check names, they appear in the API response either as free-text placeholders (no `integration_id`) or as bindings to a specific app (`integration_id: 15368` is GitHub Actions). Free-text placeholders only activate on the *next* matching workflow run and may silently fail to match if the context string drifts. Real bindings to GitHub Actions mean GitHub has already resolved each check name to a specific workflow and will gate on it immediately. Verify `integration_id: 15368` is present on all four checks after creating the ruleset — see the [verification commands](#verification-commands) section.

### 3. Variable — `DEPENDABOT_AUTOMERGE_ENABLED`

Lives at Settings → Secrets and variables → Actions → Variables tab. Value must be the literal string `"true"` to arm the workflow. Any other value (including unset) leaves the workflow disarmed.

**Why a variable, not a secret or a constant in the workflow file.** A secret is encrypted and can't be used in an `if:` condition. A constant in the workflow file means the only way to disable auto-merge is to delete or edit the workflow, which is a PR with its own CI and its own merge ceremony — far too slow for an emergency disable. A repo variable can be flipped with a single `gh variable set` command or one click in the UI, and the workflow reads it live on every event.

**Set it only after the ruleset exists and is verified.** The variable is the project's explicit opt-in statement: "I have confirmed the ruleset is in place and the safety contract is complete." Setting it before the ruleset is in place breaks the invariant (see Incident 1 below).

---

## How the components compose

The intended flow for a Dependabot PR from open to merge:

```
Dependabot opens PR
  │
  ▼
pull_request:opened event
  │
  ▼
dependabot-automerge.yml workflow fires
  │
  ▼
Guard: pull_request.user.login == 'dependabot[bot]'  ── false ──► skip (e.g. human PR)
  │ true
  ▼
Guard: vars.DEPENDABOT_AUTOMERGE_ENABLED == 'true'  ── false ──► skip (variable unset)
  │ true
  ▼
Run: gh pr merge --auto --squash $PR_URL
  │
  ▼
GitHub sets autoMergeRequest on the PR
  │
  ▼
GitHub waits for ALL ruleset conditions to be green:
  - 4 required status checks pass
  - Branch up to date with main
  - All conversations resolved
  │
  ▼
GitHub squash-merges the PR
  │
  ▼
Head branch auto-deletes (repo setting)
```

If at any point a check goes red, the PR stays open until the red check flips green (which usually requires a new commit from Dependabot, which triggers a new `synchronize` event, which re-evaluates everything). If the branch falls behind main because another PR merged first, GitHub waits for Dependabot to rebase (or a human to click "Update branch"), at which point the sync event fires and the flow continues.

---

## How each component fails if the others are missing

| Component | If missing / misconfigured | Resulting failure mode |
|---|---|---|
| Workflow | Not deployed | Dependabot PRs stay open indefinitely. Safe but manual. |
| Workflow guard uses `github.actor` | Human actions on Dependabot PRs skip the workflow | PRs stuck after any UI interaction. Observed directly (see Incident 2). |
| Workflow `--auto` flag absent | Workflow merges immediately, bypassing CI | Red PRs merge. **Never** remove the flag. |
| Ruleset | Not configured | `--auto` has nothing to wait for, merges immediately. **Red PRs merge.** Observed directly (see Incident 1). |
| Ruleset missing required status checks | Same as no ruleset | Red PRs merge. |
| Ruleset `strict: false` | Stale branches merge and then CI breaks on main | Main goes red post-hoc. |
| Variable unset or not "true" | Workflow's job skips | Dependabot PRs stay open indefinitely. Safe but manual. |
| Variable set before ruleset exists | Same as ruleset missing | Red PRs merge. |
| "Allow auto-merge" repo setting off | `gh pr merge --auto` may fail inside the workflow | Workflow runs but errors on the merge call. Graceful but noisy. |
| "Allow Actions to create/approve PRs" off | Workflow's `gh` command fails with permission error | Workflow runs but errors. Graceful but noisy. |

The invariant only holds when every component is correctly configured. The setup sequence in [docs/new-project-setup.md](new-project-setup.md) is the canonical order — follow it exactly, in order, once per new project.

---

## Incident log

### Incident 1: PR #19 — auto-merged with red CI because ruleset didn't exist yet

**Symptom.** The first grouped TanStack Dependabot PR after the `groups.tanstack` rule landed auto-merged immediately while `frontend-checks` and `api-client-checks` were **red** (`ERR_PNPM_OUTDATED_LOCKFILE`).

**Cause.** The workflow landed on main before the `main-protection` ruleset had been created. `gh pr merge --auto` waits only for the required status checks declared on the ruleset. With no ruleset, the set of required checks was empty, so `--auto` had zero conditions to wait for and merged on the spot.

**Root cause of the root cause.** The original assumption was that the `allow_auto_merge` repo setting would act as a safety fallback: "if this is off, `--auto` will fail gracefully." This was wrong. `allow_auto_merge` controls the UI button and some API scoping, but `gh pr merge --auto` with no pending checks bypasses the setting entirely and merges immediately.

**Resolution.** A follow-up PR (#18) with a lockfile fix race-landed ~50 seconds later and accidentally restored the invariant. Without that lucky race, main would have stayed red indefinitely.

**Permanent fix.** The workflow now refuses to run unless the `DEPENDABOT_AUTOMERGE_ENABLED` variable is explicitly set to `"true"`. The variable is documented as "set only after the ruleset is verified." This moves the safety check from a silent runtime behavior (`allow_auto_merge`) to an explicit action the user takes once they've verified the ruleset exists. See [ADR-010's "Safety precondition" paragraph](decisions.md#adr-010-dependabot-auto-merge-exception-to-manual-squash-rule).

### Incident 2: PRs #12–#16 — stuck after human "Update branch" clicks

**Symptom.** Five pre-existing Dependabot PRs were manually rebased against new main via the GitHub UI's "Update branch" button. The workflow fired on each resulting `synchronize` event, but every run returned `conclusion: skipped`. None of the PRs was ever queued for auto-merge. They sat in `CLEAN` mergeable state indefinitely.

**Cause.** The workflow's guard was `github.actor == 'dependabot[bot]'`. `github.actor` is whoever triggered the current event. When a human clicks "Update branch", the push is attributed to the human, so `github.actor` becomes the human, and the guard evaluates false.

**Resolution.** Hotfix PR #21 changed the guard to `github.event.pull_request.user.login == 'dependabot[bot]'`, which reads the PR author from the event payload. That field stays `dependabot[bot]` for the lifetime of the PR regardless of who triggers any given event on it.

**Permanent fix.** The fixed guard is now in the workflow file with an inline comment explaining why, plus the [ADR-010 guard-condition paragraph](decisions.md#adr-010-dependabot-auto-merge-exception-to-manual-squash-rule). The pattern must not be reintroduced on future edits.

**Why this trap is common.** GitHub's own Dependabot auto-merge documentation recommended `github.actor` for about a year before being updated in 2023. The wrong pattern propagated to hundreds of public workflow examples. Any project adopting this pattern from a search result or AI completion should verify which field the guard reads.

### Incident 3: PR #16 — rebase conflict cascade on sibling pyproject.toml bumps

**Symptom.** After PRs #12, #13, #14, #15 each merged bumps to adjacent lines in [apps/backend/pyproject.toml](../apps/backend/pyproject.toml) (`testcontainers`, `sqlalchemy`, `alembic`, `schemathesis`), PR #16 (`asyncpg`) could not be auto-rebased. `PUT /update-branch` returned `422 merge conflict between base and head`.

**Cause.** Each of the four merged PRs modified a distinct line in the same file, but the lines were close enough together that git's 3-way merge could not cleanly reapply PR #16's change on top of the cumulative post-merge state. The conflict was contextual, not semantic — the `asyncpg` bump itself was a trivial one-line edit.

**Resolution.** PR #16 was closed with an explanatory comment. A manual replacement PR (#22) was opened with the same one-line `asyncpg` bump against current main. CI passed, human squash-merged.

**Permanent fix.** The [.github/dependabot.yml](../.github/dependabot.yml) `sqlalchemy-stack` group now batches `sqlalchemy`, `alembic`, `asyncpg` into a single atomic PR. Future bumps of any of the three will move as a unit and cannot cascade-conflict with siblings. The same grouping strategy is applied to every other interlocking ecosystem in the manifest.

**Takeaway.** When Dependabot opens many sibling PRs touching the same manifest file, the fifth one is almost always doomed. The fix is never "try to merge it manually"; the fix is always "group the ecosystem in dependabot.yml so there are no siblings." Incident 3 is the strongest argument for aggressive grouping in this repo's dependabot.yml.

---

## Operational runbook

### Verification commands

Run these after any change to the auto-merge system to confirm the paired contract is intact:

```bash
# 1. Ruleset exists and is active
gh api repos/<owner>/<repo>/rulesets --jq '.[] | select(.name=="main-protection") | {name, enforcement, id}'
# Expect: {"name":"main-protection","enforcement":"active","id":<number>}

# 2. Ruleset has all 4 required status checks bound to GitHub Actions
gh api repos/<owner>/<repo>/rulesets/<id> \
  --jq '.rules[] | select(.type=="required_status_checks") | .parameters.required_status_checks'
# Expect: 4 entries, each with "context" and "integration_id": 15368

# 3. Variable is set
gh variable list --repo <owner>/<repo>
# Expect: DEPENDABOT_AUTOMERGE_ENABLED    true

# 4. Repo merge settings
gh api repos/<owner>/<repo> --jq '{allow_auto_merge, allow_merge_commit, allow_rebase_merge, allow_squash_merge, delete_branch_on_merge}'
# Expect: allow_auto_merge=true, allow_merge_commit=false, allow_rebase_merge=false,
#         allow_squash_merge=true, delete_branch_on_merge=true

# 5. Workflow file present on main
gh api repos/<owner>/<repo>/contents/.github/workflows/dependabot-automerge.yml --jq '.name'
# Expect: "dependabot-automerge.yml"
```

### How to disable auto-merge in an emergency

Do not remove the workflow file, do not delete the ruleset, do not change branch protection. The intended emergency-disable mechanism is to flip the variable:

```bash
gh variable set DEPENDABOT_AUTOMERGE_ENABLED --body "false" --repo <owner>/<repo>
```

This is effective on the next `pull_request` event. All Dependabot PRs that would have auto-merged will instead sit open waiting for manual squash. The workflow itself continues to run; it just reports `skipped` on every event. The ruleset continues to gate merges for both humans and Dependabot normally.

To re-enable, set the variable back to `"true"`.

### How to unstick a Dependabot PR

Several patterns, in order of preference:

**1. If the PR is `BEHIND` main (stale base),** call the server-side update-branch endpoint:

```bash
gh api -X PUT repos/<owner>/<repo>/pulls/<number>/update-branch
```

This is the preferred path. It performs the rebase server-side, emits a `synchronize` event that fires the auto-merge workflow, and respects the repo's configured merge method. Works even when Dependabot has "disavowed" the PR (see next).

**2. If Dependabot has disavowed the PR** (comment: "Looks like this PR has been edited by someone other than Dependabot"), `@dependabot rebase` will not work. Dependabot's self-rebase refuses to operate on PRs it thinks have been touched by humans. Use `PUT /update-branch` instead — it is not owned by Dependabot and works regardless of the disavowal state.

**3. If the PR has a rebase conflict** (Incident 3 pattern), close it with an explanatory comment and open a manual replacement PR. Do not attempt to force-rebase. The `sqlalchemy-stack`-style grouping in dependabot.yml should prevent this class of conflict going forward; if you hit it, it's a signal that another group is missing.

**4. If the workflow is reporting `skipped` when you expect it to run,** the guard is returning false. Check in order: (a) is the PR author `dependabot[bot]` (not some other bot)? (b) is `DEPENDABOT_AUTOMERGE_ENABLED` set to the literal string `"true"`? Query with `gh variable list`. (c) is the workflow file still present on main with the correct guard condition? Compare against this repo's [.github/workflows/dependabot-automerge.yml](../.github/workflows/dependabot-automerge.yml).

**5. If CI is red on the PR,** the auto-merge is correctly refusing to merge. Investigate the failing check. Common causes:
   - `frontend-checks` with `ERR_PNPM_OUTDATED_LOCKFILE`: Dependabot modified `package.json` but not `pnpm-lock.yaml`. Close the PR, bump manually with `pnpm update --latest`, open a replacement that touches both files.
   - `backend-checks` with an `uv sync` error: usually `pyproject.toml` / `uv.lock` divergence. Same pattern — close, manual bump, replacement.
   - A genuine test failure introduced by the dependency bump: investigate as a real regression. Either pin the old version, or fix the code that broke.

### How to unstick a cascade of stuck PRs

When multiple PRs are in the queue and the first one merges, main advances and the remaining PRs become `BEHIND`. The "update-branch + wait + merge" cycle must then be repeated for each. Dependabot will rebase the next one in line on its own schedule, or you can cascade manually:

```bash
for pr in <list of stuck PR numbers>; do
  echo "Processing #$pr..."
  gh api -X PUT repos/<owner>/<repo>/pulls/$pr/update-branch
  gh pr checks $pr --repo <owner>/<repo> --watch --fail-fast
  # The merge happens automatically via the queue once checks pass
done
```

Each iteration takes about 60–90 seconds end-to-end. The `--watch` blocks until checks resolve, giving the auto-merge time to fire before starting the next.

### The Dependabot lockfile gap — fixed at template level by the sync workflow

Dependabot's version update ecosystem support for pnpm workspaces has a known bug: when a monorepo uses a single root `pnpm-lock.yaml` and per-workspace `package.json` files, Dependabot updates only the manifest and fails to regenerate the root lockfile. CI then rejects the PR with `ERR_PNPM_OUTDATED_LOCKFILE` when `pnpm install --frozen-lockfile` runs. The backend has a looser equivalent: `uv sync --dev` regenerates `uv.lock` on the fly, so the divergence doesn't break CI, but the lockfile in git is still silently out of sync after every Dependabot merge.

**The template now ships a fix:** [.github/workflows/dependabot-lockfile-sync.yml](../.github/workflows/dependabot-lockfile-sync.yml). See the "Lockfile sync workflow" section below for the architecture and the setup steps. Once enabled, the close-and-replace manual workflow is no longer needed — Dependabot PRs that hit the lockfile-gap bug are auto-fixed within 2–3 minutes of being opened, with zero human intervention, and then proceed through the normal auto-merge pipeline.

**Manual fallback if the sync workflow isn't enabled** (or fails for any reason): close the broken PR, run `pnpm update --latest <packages>` (or `uv lock` for backend) locally from the workspace directory, commit both the updated manifest and the regenerated lockfile atomically, open a replacement PR.

---

## The lockfile sync workflow

[.github/workflows/dependabot-lockfile-sync.yml](../.github/workflows/dependabot-lockfile-sync.yml) is a second workflow that composes with `dependabot-automerge.yml` to make Dependabot PRs self-healing against the lockfile-gap bug.

### What it does

Fires on every `pull_request` event (`opened`, `synchronize`, `reopened`). The job's `if:` guard scopes it to Dependabot-authored PRs where `vars.DEPENDABOT_LOCKFILE_SYNC_ENABLED == 'true'`. If the guard passes, the workflow:

1. **Verifies the PAT secret exists** and fails loudly if it doesn't (see "Why a PAT is required" below).
2. **Checks out the PR branch** using the PAT as the git token.
3. **Guards against self-triggered loops** — if the most recent commit was authored by `github-actions[bot]@users.noreply.github.com`, the workflow skips without doing anything. This is the infinite-loop prevention: the workflow's own push becomes the head commit; the resulting `synchronize` event fires the workflow again; the loop guard detects its own authorship and exits cleanly.
4. **Detects which manifests changed** by diffing `base.sha..head.sha`. Produces three flags: `needs_pnpm`, `needs_uv_backend`, `needs_uv_error_contracts`. The workflow only runs the package managers that actually have work to do.
5. **Regenerates the pnpm lockfile** with `pnpm install --no-frozen-lockfile --lockfile-only` (skip installing `node_modules`, just update the lockfile).
6. **Regenerates the uv lockfiles** with `uv lock` inside `apps/backend` and/or `packages/error-contracts` as needed.
7. **Stages and commits** any lockfile changes. If `git diff --cached --quiet`, the workflow exits cleanly — there's nothing to push.
8. **Pushes** the update back to the PR branch using the PAT.

The push triggers a new `pull_request.synchronize` event, which fires the workflow again (no-op, loop guard catches it) plus the auto-merge workflow and CI. CI now runs against the corrected lockfile and passes. Auto-merge queue sees all required checks green and executes the squash-merge.

### Why a PAT is required (not `GITHUB_TOKEN`)

The workflow **must** push using a Personal Access Token stored in `secrets.DEPENDABOT_LOCKFILE_SYNC_PAT`. `GITHUB_TOKEN` would not work because of a deliberate GitHub security limitation:

> "When you use the repository's `GITHUB_TOKEN` to perform tasks, events triggered by the `GITHUB_TOKEN`, with the exception of `workflow_dispatch` and `repository_dispatch`, will not create a new workflow run."

In other words: if the sync workflow pushes with `GITHUB_TOKEN`, the resulting push advances the PR head but does not trigger `ci.yml` to re-run on the new commit. The PR's required status checks stay attached to the old, broken commit. The ruleset then refuses to merge because no checks are present on the head commit. The PR sits stuck indefinitely.

Pushing with a PAT (or a GitHub App installation token) bypasses this limitation because the token looks like a normal user to GitHub's workflow dispatcher.

**Use a fine-grained PAT**, not a classic PAT. Fine-grained PATs can be scoped to a single repository with minimal permissions:

- **Repository access:** select exactly the one repo that needs the sync workflow
- **Permissions:**
  - `Contents: Read and write`
  - `Pull requests: Read and write`
- **Expiration:** set a reasonable limit (e.g., 1 year) and rotate on expiration

Store it as a repo secret named `DEPENDABOT_LOCKFILE_SYNC_PAT` (Settings → Secrets and variables → Actions → Secrets tab → New repository secret). The workflow references it via `secrets.DEPENDABOT_LOCKFILE_SYNC_PAT`.

### Prerequisites for the sync workflow to function

All three must be satisfied. The workflow reports `conclusion: skipped` if any are missing (except the PAT check, which is an explicit step that fails loudly with a directive error message so you know exactly what's missing):

1. **Repo variable** `DEPENDABOT_LOCKFILE_SYNC_ENABLED` set to literal string `"true"`.
2. **Repo secret** `DEPENDABOT_LOCKFILE_SYNC_PAT` containing a fine-grained PAT as described above.
3. **"Allow GitHub Actions to create and approve pull requests"** enabled under Settings → Actions → General → Workflow permissions. This is already required for `dependabot-automerge.yml`, so it's assumed here too.

See [new-project-setup.md Phase 5b](new-project-setup.md) for step-by-step setup.

### How the sync workflow composes with the auto-merge workflow

They are independent but compatible. Both fire on the same `pull_request` events. The order in which they fire doesn't matter:

- **Sync fires first** (or in parallel): regenerates lockfile, pushes fix. New synchronize event fires both workflows again.
- **Auto-merge fires first** (or in parallel): calls `gh pr merge --auto --squash`, queues the PR for auto-merge. GitHub's queue then waits for all required checks. When the sync workflow's push lands and CI re-runs with correct checks, the queue releases the merge.

Either sequence converges to the same end state in roughly the same wall clock time. The combined effect is: **a Dependabot PR with the lockfile-gap bug is auto-corrected and auto-merged end-to-end with zero human intervention**, in about 2–3 minutes of wall clock time.

### Emergency disable

```bash
gh variable set DEPENDABOT_LOCKFILE_SYNC_ENABLED --body "false"
```

Effective on the next `pull_request` event. The workflow continues to run on every event but reports `skipped` instead of doing anything. The auto-merge workflow is not affected; PRs that would have been lockfile-fixed will now sit stuck with red CI again until either you flip the variable back on, you manually run `pnpm update`/`uv lock` and push, or you close and manually replace the PR.

---

## Related reading

- [ADR-010: Dependabot Auto-Merge Exception to Manual-Squash Rule](decisions.md#adr-010-dependabot-auto-merge-exception-to-manual-squash-rule) — the design decision and its rationale
- [docs/new-project-setup.md Phase 3](new-project-setup.md) — merge method settings the auto-merge workflow depends on
- [docs/new-project-setup.md Phase 4](new-project-setup.md) — the `main-protection` ruleset setup
- [docs/new-project-setup.md Phase 5](new-project-setup.md) — workflow permissions and `DEPENDABOT_AUTOMERGE_ENABLED` variable
- [docs/new-project-setup.md Phase 5b](new-project-setup.md) — `DEPENDABOT_LOCKFILE_SYNC_ENABLED` variable + PAT setup for the lockfile sync workflow
- [.github/workflows/dependabot-automerge.yml](../.github/workflows/dependabot-automerge.yml) — the auto-merge workflow with inline comments
- [.github/workflows/dependabot-lockfile-sync.yml](../.github/workflows/dependabot-lockfile-sync.yml) — the lockfile sync workflow with inline comments
- [.github/dependabot.yml](../.github/dependabot.yml) — grouping configuration with rationale comments
- [CLAUDE.md](../CLAUDE.md) — Dependabot handling rules in the discipline contract
