# New Project Setup

Step-by-step guide for initializing a new project from this template. Do these steps **in order** — several steps depend on earlier ones, and skipping ahead will leave you locked out of your own repo or with rules that silently fail to enforce.

---

## Phase 1: Create the repo

- [ ] Click **Use this template** → **Create a new repository** on GitHub
- [ ] Fill in:
  - **Owner**: your org or personal account
  - **Repository name**: your project name
  - **Visibility**: Private (recommended)
  - **Include all branches**: unchecked (you only need `main`)
- [ ] Click **Create repository**
- [ ] Clone locally: `git clone git@github.com:<owner>/<repo>.git && cd <repo>`
- [ ] Verify you're on `main` and it's clean: `git status`

---

## Phase 2: Enable CodeQL (do this BEFORE branch rules)

**Location:** Settings → Code security → Code scanning

- [ ] Click **Set up** next to "CodeQL analysis"
- [ ] Choose **Default**
- [ ] GitHub auto-detects Python + TypeScript
- [ ] Click **Enable CodeQL**

*Why first: CodeQL needs to run once before you can require its results in a ruleset. Enabling it now means by the time you open your first PR, it has a baseline to analyze against.*

---

## Phase 3: Configure merge methods

**Location:** Settings → General → scroll to **Pull Requests** section

- [x] **Allow squash merging**
- [ ] **Allow merge commits** (uncheck)
- [ ] **Allow rebase merging** (uncheck)
- Default commit message: **Pull request title and description**
- [x] **Always suggest updating pull request branches**
- [x] **Allow auto-merge** *(required by the Dependabot auto-merge workflow — see Phase 5)*
- [x] **Automatically delete head branches**

Click **Save**.

**Every merge in this repo uses the green "Squash and merge" button. Always. No exceptions — except automated Dependabot auto-merge for PRs where every required status check is green, documented in [decisions.md ADR-010](decisions.md) and [automerge.md](automerge.md).**

Consequence: since PR titles become permanent commit subjects on `main`, write them like commit messages:

- `feat(widgets): add pagination to widget list`
- `fix(auth): handle expired refresh token in middleware`
- `chore(deps): bump pydantic 2.9.0 -> 2.10.0`

Not `updates`, not `fixing the thing`, not `wip`.

---

## Phase 4: Create the branch ruleset for `main`

**Location:** Settings → Rules → Rulesets → **New branch ruleset**

### Top of form

- [ ] **Ruleset name:** `main-protection`
- [ ] **Enforcement status:** `Active`
- [ ] **Bypass list:** *(leave empty — yes, even for yourself. The contract applies to you too.)*
- [ ] **Target branches:** click "Add target" → **Include default branch**

### Rules section — check these

- [x] **Restrict deletions**
- [x] **Require linear history**
- [x] **Require a pull request before merging**
  - Required approvals: **0** (solo dev) or **1+** (team)
  - [x] Dismiss stale pull request approvals when new commits are pushed
  - [x] Require conversation resolution before merging
  - [ ] Require review from Code Owners
  - [ ] Require approval of the most recent reviewable push
- [x] **Require status checks to pass**
  - [x] Require branches to be up to date before merging
  - [ ] Do not require status checks on creation
  - Add these 4 checks (type as free text, press Enter after each):
    - `backend-checks`
    - `frontend-checks`
    - `api-client-checks`
    - `error-contracts`
- [x] **Block force pushes**

### Rules section — leave UNCHECKED

- [ ] Restrict creations
- [ ] Restrict updates
- [ ] Require signed commits *(unless GPG/SSH signing is already configured)*
- [ ] Require deployments to succeed
- [ ] Require code scanning results *(come back in Phase 8)*
- [ ] Require code quality results
- [ ] Automatically request Copilot code review *(the [copilot-review.yml](../.github/workflows/copilot-review.yml) workflow handles this)*

Click **Create** at the bottom.

> **Note on the 4 check names:** GitHub's picker only autocompletes names it has seen run before. On a fresh template repo, type them as free text — GitHub accepts "Expected" placeholders that activate when the first matching check runs. **Verify after creating the ruleset** that each of the 4 check entries in the API response has `integration_id: 15368` (the GitHub Actions app ID) and not `null`. If the picker autocompleted from an existing workflow run, you'll see `15368` — the check is a real binding and the rule is already live. If the check is a free-text placeholder, `integration_id` will be `null` and it will only activate on the *next* matching workflow run; until then the check name effectively does not gate merges. Query with:
>
> ```bash
> gh api repos/OWNER/REPO/rulesets/RULESET_ID \
>   --jq '.rules[] | select(.type=="required_status_checks") | .parameters.required_status_checks'
> ```

---

## Phase 5: GitHub Actions & Dependabot config

**Location:** Settings → Actions → General

- [x] **Actions permissions:** "Allow all actions and reusable workflows"
- [x] **Workflow permissions:** **"Read and write permissions"** *(not "Read repository contents" — the Dependabot auto-merge workflow calls `gh pr merge --auto` which requires write access to the PR)*
- [x] **Allow GitHub Actions to create and approve pull requests**

Without the "Allow Actions to create and approve pull requests" toggle, `gh pr merge --auto` inside a workflow fails with a permission error on every run. Without "Read and write permissions" on workflows, the same call fails earlier with a token-scope error. Both toggles are hard prerequisites for the auto-merge workflow — the workflow does not feature-detect them.

**Location:** Settings → Code security

- [x] **Dependency graph:** enabled *(prerequisite for the two below — in the same section, enable this first)*
- [x] **Dependabot alerts:** enabled
- [x] **Dependabot security updates:** enabled
- [x] **Grouped security updates:** enabled *(optional but recommended — batches CVE patches the same way [dependabot.yml](../.github/dependabot.yml) batches version bumps)*

The [.github/dependabot.yml](../.github/dependabot.yml) ships with aggressive `groups:` rules for every interlocking ecosystem (TanStack, React, Storybook, Vitest, Testing Library, Tailwind, i18next, Dinero, Pydantic, SQLAlchemy stack, Pytest, FastAPI stack, openapi, github-official-actions). This keeps the weekly PR count low and prevents the rebase-conflict cascades that happen when sibling PRs touch adjacent lines in the same manifest — see [automerge.md Incident 3](automerge.md#incident-3-pr-16--rebase-conflict-cascade-on-sibling-pyprojecttoml-bumps).

---

## Phase 5a: Arm Dependabot auto-merge (repo variable)

**Prerequisites:** Phase 4 (ruleset) must be complete and **verified**. Phase 5 (workflow permissions + alerts) must be complete. If either is missing, do not proceed — [Incident 1](automerge.md#incident-1-pr-19--auto-merged-with-red-ci-because-ruleset-didnt-exist-yet) explains why.

**Location:** Settings → Secrets and variables → Actions → **Variables** tab → **New repository variable**

- **Name:** `DEPENDABOT_AUTOMERGE_ENABLED`
- **Value:** `true` *(the literal string — the workflow's `if:` compares with `== 'true'`)*

Or via CLI:

```bash
gh variable set DEPENDABOT_AUTOMERGE_ENABLED --body "true"
```

**What this does.** The [.github/workflows/dependabot-automerge.yml](../.github/workflows/dependabot-automerge.yml) workflow's `if:` guard reads this variable and evaluates false unless it is literally `"true"`. Setting it here arms the workflow to call `gh pr merge --auto --squash` on every Dependabot PR. GitHub's native merge queue then merges each such PR when every required status check on the `main-protection` ruleset is green and every conversation is resolved.

**Why a variable, not a constant in the workflow file.** The variable is the emergency kill switch. If auto-merge ever starts misbehaving, flip the variable to `"false"` with one command (`gh variable set DEPENDABOT_AUTOMERGE_ENABLED --body "false"`) and the workflow instantly becomes a no-op on every event. No workflow file edit, no PR, no CI wait. This is by design — see [automerge.md, "How to disable auto-merge in an emergency"](automerge.md#how-to-disable-auto-merge-in-an-emergency).

**Verify the variable took effect.** After setting:

```bash
gh variable list
# Expect: DEPENDABOT_AUTOMERGE_ENABLED    true    <timestamp>
```

Then open any new Dependabot PR (or wait for the next weekly run). Watch the workflow in the Actions tab — the `automerge` job should have `status: completed, conclusion: success` (not `skipped`) after running `gh pr merge --auto --squash`. If it still says `skipped`, the guard evaluated false — check in order: (1) is the PR authored by `dependabot[bot]`? (2) is the variable literally `"true"` and not `"True"` or `"yes"` or empty? (3) is the workflow file present on main?

---

## Phase 5b: Arm Dependabot lockfile sync (PAT + variable)

**Prerequisites:** Phase 5 (workflow permissions) and Phase 5a (auto-merge variable) must be complete. Phase 4 (ruleset) must be active.

**Why this phase exists.** Dependabot has a known bug with pnpm workspaces: when a monorepo uses a single root `pnpm-lock.yaml` with per-workspace `package.json` files, Dependabot updates only the manifest and silently fails to regenerate the lockfile. CI then rejects the PR with `ERR_PNPM_OUTDATED_LOCKFILE`. Without this phase, every weekly Dependabot PR that touches frontend deps will sit stuck and require you to manually close + recreate it with `pnpm update --latest` + regenerated lockfile. The template ships a fix: [.github/workflows/dependabot-lockfile-sync.yml](../.github/workflows/dependabot-lockfile-sync.yml) fires on Dependabot PRs, regenerates the lockfile, and pushes the fix back — but it needs authentication that can re-trigger CI on the pushed commit, and that authentication must be a Personal Access Token (PAT) rather than the built-in `GITHUB_TOKEN`. This phase sets up the PAT.

See [automerge.md, "The lockfile sync workflow"](automerge.md#the-lockfile-sync-workflow) for the full architecture and the reasoning behind the PAT requirement.

### Step 1 — Create a fine-grained Personal Access Token

**Location:** https://github.com/settings/personal-access-tokens/new

- **Token name:** `fe-be-repo-model dependabot lockfile sync` *(or match your actual repo name)*
- **Expiration:** 1 year *(set a calendar reminder to rotate)*
- **Description:** `Used by .github/workflows/dependabot-lockfile-sync.yml to push regenerated lockfiles back to Dependabot PRs. Pushes must be authored by a non-GITHUB_TOKEN identity to re-trigger CI.`
- **Resource owner:** your account
- **Repository access:** **Only select repositories** → pick exactly the one project repo you're setting up
- **Repository permissions:**
  - **Contents:** **Read and write**
  - **Pull requests:** **Read and write**
  - All others: **No access**
- Click **Generate token**
- Copy the token immediately — you will not see it again

**Do not use a classic PAT.** Classic PATs are account-wide and have excessive scopes; fine-grained PATs are scoped to individual repos with individual permissions. If your GitHub account has never created a fine-grained PAT before, you may need to enable the feature under Settings → Developer settings → Personal access tokens.

### Step 2 — Store the PAT as a repo secret

**Location:** Settings → Secrets and variables → Actions → **Secrets** tab → **New repository secret**

- **Name:** `DEPENDABOT_LOCKFILE_SYNC_PAT`
- **Secret:** paste the token from Step 1
- Click **Add secret**

Or via CLI:

```bash
gh secret set DEPENDABOT_LOCKFILE_SYNC_PAT --body "ghp_your_token_here"
```

### Step 3 — Arm the lockfile sync workflow

**Location:** Settings → Secrets and variables → Actions → **Variables** tab → **New repository variable**

- **Name:** `DEPENDABOT_LOCKFILE_SYNC_ENABLED`
- **Value:** `true`

Or via CLI:

```bash
gh variable set DEPENDABOT_LOCKFILE_SYNC_ENABLED --body "true"
```

### Step 4 — Verify both are in place

```bash
gh secret list | grep DEPENDABOT_LOCKFILE_SYNC_PAT
# Expect: DEPENDABOT_LOCKFILE_SYNC_PAT    <timestamp>

gh variable list | grep DEPENDABOT_LOCKFILE_SYNC_ENABLED
# Expect: DEPENDABOT_LOCKFILE_SYNC_ENABLED    true    <timestamp>
```

### Step 5 — Test it on the next Dependabot PR

Wait for the next weekly Dependabot run, or trigger one manually via Insights → Dependency graph → Dependabot → "Check for updates". When a PR opens:

- The `Dependabot lockfile sync` workflow should run and either (a) report `conclusion: success` with a visible "Commit and push lockfile updates" step if the PR had the bug, or (b) report `conclusion: success` with "No lockfile changes to commit" if Dependabot happened to get it right.
- After the sync workflow pushes a fix commit, CI re-runs on the new head, turns green, and the auto-merge workflow (from Phase 5a) squash-merges the PR without human intervention.

If anything goes wrong, the most likely failure is the PAT check. The first step of the sync workflow is `Verify PAT is configured`, which fails loudly with a specific error message directing you back to this phase. Any other step failure usually indicates the sync workflow is broken on this repo — file an issue and fall back to the manual replacement pattern described in [automerge.md, "If my Dependabot PR has red CI"](automerge.md#my-dependabot-pr-has-red-ci--err_pnpm_outdated_lockfile-or-uv-sync-error).

### Emergency disable

If the sync workflow starts behaving badly, kill it without touching files:

```bash
gh variable set DEPENDABOT_LOCKFILE_SYNC_ENABLED --body "false"
```

Effective on the next `pull_request` event. Does not affect the auto-merge workflow (separately controlled by `DEPENDABOT_AUTOMERGE_ENABLED`). The consequence is: Dependabot PRs with the lockfile-gap bug will sit stuck again until you manually fix them or re-enable the variable.

---

## Phase 6: Bootstrap local environment

### Prerequisites

| Tool | Version | Install |
|---|---|---|
| Python | 3.13 | `pyenv install 3.13` or system |
| Node.js | 22 LTS | `nvm install 22` or system |
| pnpm | 10 | `npm install -g pnpm@10` |
| uv | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Docker + Compose | latest | Docker Desktop or system |
| Task | latest | `brew install go-task` or [install guide](https://taskfile.dev/installation/) |
| pre-commit | latest | `pip install pre-commit` |

### First-time setup

```bash
# 1. Install pre-commit hooks
pre-commit install
pre-commit install --hook-type pre-push

# 2. Install backend dependencies
cd apps/backend
uv sync --dev

# 3. Create backend .env
cp ../../.env.example .env
# Edit .env — set DATABASE_URL to your local Postgres or leave default

# 4. Install frontend dependencies
cd ../frontend
pnpm install

# 5. Install error-contracts dependencies
cd ../../packages/error-contracts
uv sync --dev

# 6. Verify everything works
cd ../..
task check
```

If `task check` fails on a fresh clone, the template is broken — fix it before continuing. Don't work around it.

### Running locally

```bash
# Option A: Full stack via Docker
task dev

# Option B: Individual services
task dev:backend   # Backend on :8000
task dev:frontend  # Frontend on :5173 (proxies /api to :8000)

# Storybook
task storybook     # Storybook on :6006
```

---

## Phase 7: Rename the project

This rename will become your first PR (Phase 8), which bootstraps the required status checks.

- [ ] **Backend**
  - [ ] `apps/backend/pyproject.toml`: change `name` from `project-template-backend`
  - [ ] `apps/backend/app/main.py`: change `title` in `FastAPI(title="...")`
- [ ] **Frontend**
  - [ ] `apps/frontend/package.json`: change `name` from `@repo/frontend`
  - [ ] `apps/frontend/src/i18n/locales/en/common.json`: change `app_name`
  - [ ] `apps/frontend/src/i18n/locales/ro/common.json`: change `app_name`
- [ ] **Docker**
  - [ ] `infra/docker/backend.Dockerfile`: change image labels if needed
  - [ ] `infra/docker/frontend.Dockerfile`: change image labels if needed
  - [ ] `.github/workflows/deploy.yml`: change image names
- [ ] **Docs**
  - [ ] `README.md`: update project description
  - [ ] `CONTRIBUTING.md`: update project-specific instructions

---

## Phase 8: Database setup

```bash
# Start Postgres (if not using Docker)
task docker:up   # or your local Postgres

# Run migrations
task db:migrate

# Create a new migration after model changes
task db:revision -- "add_users_table"
```

---

## Phase 9: Bootstrap first PR

The 4 required status checks you added in Phase 4 exist as "Expected" placeholders until GitHub sees them run at least once. Your first PR activates them.

- [ ] Create a branch for the rename: `git checkout -b chore/initial-setup`
- [ ] Stage the rename changes from Phase 7: `git add -A`
- [ ] Commit: `git commit -m "chore: initial project setup"`
- [ ] Push: `git push -u origin chore/initial-setup`
- [ ] Open a PR via `gh pr create` or the GitHub UI (base: `main`)
- [ ] Wait for all 4 checks to pass:
  - [ ] `backend-checks`
  - [ ] `frontend-checks`
  - [ ] `api-client-checks`
  - [ ] `error-contracts`
- [ ] Wait for CodeQL analysis to complete (appears as a separate check)
- [ ] Click the green **Squash and merge** button
- [ ] Verify the commit subject on `main` matches your PR title
- [ ] Verify the head branch auto-deleted after merge

If any check fails on this trivial PR, your template's CI is broken on bootstrap — fix it before adding real work.

---

## Phase 10: Add CodeQL to the ruleset

Now that CodeQL has produced results on your first PR, enable the requirement.

**Location:** Settings → Rules → Rulesets → click `main-protection` → **Edit**

- [x] **Require code scanning results**
  - Tool: `CodeQL`
  - Alert severity threshold: **High or higher**
  - Security alerts threshold: **High or higher**

Click **Save changes**.

---

## Phase 11: Sanity-check the setup

Verify everything is wired correctly by attempting to violate each rule. If any of these *succeed* when they should fail, your ruleset isn't active — go back to Phase 4 and check the enforcement status.

### Ruleset tests (should all fail with `GH013: Repository rule violations`)

- [ ] Try pushing directly to `main`:
  ```bash
  git commit --allow-empty -m "sanity-check"
  git push origin main   # should be BLOCKED
  git reset --hard origin/main   # undo local commit after
  ```
- [ ] Try force-pushing to `main`:
  ```bash
  git push --force origin main   # should be BLOCKED
  ```
- [ ] Try deleting `main` → should be **blocked** *(skip actually running this — API evidence that the `deletion` rule exists is sufficient; accidentally succeeding would orphan the whole repo)*
- [ ] Open a PR with a merge commit in history → should be **blocked** by the `required_linear_history` rule
- [ ] Open a PR, watch all 4 status checks + CodeQL → merge button only enabled when all green

### Auto-merge tests (once Phase 5a is done)

- [ ] Open a non-Dependabot PR. Verify the `Dependabot auto-merge` workflow fires and the `automerge` job reports `conclusion: skipped` (the `pull_request.user.login` clause of the guard returns false on a human PR).
- [ ] Open or wait for a Dependabot PR with all 4 checks green. Verify the `automerge` job reports `conclusion: success` (not `skipped`) and the PR's `autoMergeRequest` field is populated. The PR should then auto-squash-merge without a human clicking merge.
- [ ] If a Dependabot PR needs to be rebased (`BEHIND` main), use the server-side endpoint — NOT the "Update branch" button (see [automerge.md, Incident 2](automerge.md#incident-2-prs-1216--stuck-after-human-update-branch-clicks)):
  ```bash
  gh api -X PUT repos/OWNER/REPO/pulls/NUMBER/update-branch
  ```

Two minutes of deliberate violation testing saves you from discovering months later that someone force-pushed over your history because a rule was never actually enforced.

---

## Phase 12: Repo hygiene settings

**Location:** Settings → General

- [ ] **Template repository** (unchecked — this is the *new* repo, not the template itself)
- [x] **Issues** enabled
- [x] **Preserve this repository** (optional but recommended)
- [ ] **Wikis** (disabled — docs live in the repo)
- [ ] **Projects** (enable only if you use GitHub Projects)
- [ ] **Discussions** (disabled by default — enable later if needed)

---

## CI Pipeline Reference

The CI pipeline runs automatically on PRs to `main`. It runs:

| Job | What it checks |
|---|---|
| `backend-checks` | ruff lint, ruff format, pyright, import-linter, unit + integration tests with coverage |
| `frontend-checks` | biome, eslint i18n, tsc, vitest with coverage, storybook build |
| `api-client-checks` | Extracts OpenAPI spec, generates schema.d.ts, verifies no drift |
| `error-contracts` | Codegen tests, validates translations match error codes |

The deploy workflow ([deploy.yml](../.github/workflows/deploy.yml)) triggers on push to `main` and builds Docker images. Customize the push/deploy steps for your infrastructure.

The copilot-review workflow ([copilot-review.yml](../.github/workflows/copilot-review.yml)) requests a Copilot review on every non-draft PR. It has `continue-on-error: true` — failures don't block merges. Do NOT add it to required status checks and do NOT enable "Automatically request Copilot code review" in the ruleset. One mechanism, one source of truth.

---

## First Feature Checklist

When building your first feature beyond the Widget example:

- [ ] Create `app/features/<name>/` with model, repository, service, router, schemas/
- [ ] Add model import to `alembic/env.py`
- [ ] Run `task db:revision -- "create_<name>_table"`
- [ ] Add error codes to `packages/error-contracts/errors.yaml`
- [ ] Run `task errors:generate`
- [ ] Add translations to ALL `apps/frontend/src/i18n/locales/*/errors.json`
- [ ] Run `task errors:check`
- [ ] Add feature to import-linter independence contract
- [ ] Create frontend feature: `src/features/<name>/api/` + `components/`
- [ ] Run `task client:generate` to update TypeScript types
- [ ] Run `task check` before committing

---

## Deleting the Widget Example

Once you're comfortable with the patterns, delete the Widget feature:

```bash
# Backend
rm -rf apps/backend/app/features/widget/
rm -rf apps/backend/tests/unit/features/widget/
rm -rf apps/backend/tests/integration/features/widget/

# Frontend
rm -rf apps/frontend/src/features/widgets/

# Remove widget error codes from errors.yaml
# Remove widget translations from locales/*/errors.json
# Remove widget import from alembic/env.py
# Run task errors:generate
# Create a migration to drop the widgets table
# Update import-linter contracts to remove widget references
# Update the widgets route in src/routes/$lang/widgets/
```

Keep the rest — `BaseRepository`, `BaseService`, shared components, error system, i18n, and infrastructure are the template's value.

---

## What NOT to do

- ~~Add yourself to the ruleset bypass list~~ — defeats the discipline contract
- ~~Enable "Require signed commits"~~ unless you already have GPG/SSH signing configured
- ~~Enable "Automatically request Copilot code review" in the ruleset~~ — duplicates [copilot-review.yml](../.github/workflows/copilot-review.yml)
- ~~Make `request-copilot-review` a required status check~~ — it's advisory with `continue-on-error: true`, gating merges on it is pointless
- ~~Merge the first PR before CI completes~~ — you need CI to run to register the check names
- ~~Set required approvals > 0 as a solo dev~~ — you'll lock yourself out
- ~~Use "Merge commit" or "Rebase and merge"~~ — linear history rule forbids one, squash-only is better than the other
- ~~Skip Phase 2 (CodeQL) and do it last~~ — you'll hit a chicken-and-egg problem requiring a check that has never produced results
- ~~Classic branch protection (Settings → Branches)~~ — use Rulesets (Settings → Rules) instead; never mix both
- ~~Set `DEPENDABOT_AUTOMERGE_ENABLED=true` before verifying the ruleset is active~~ — `gh pr merge --auto` waits only for checks declared on the ruleset; with no ruleset, `--auto` has nothing to wait for and merges immediately, including red PRs. See [automerge.md Incident 1](automerge.md#incident-1-pr-19--auto-merged-with-red-ci-because-ruleset-didnt-exist-yet)
- ~~Use `github.actor` in any auto-merge workflow guard~~ — reads the current event's triggerer, not the PR author, and silently skips the workflow whenever a human interacts with a Dependabot PR. Always read `github.event.pull_request.user.login`. See [automerge.md Incident 2](automerge.md#incident-2-prs-1216--stuck-after-human-update-branch-clicks)
- ~~Click "Update branch" in the UI to rebase a Dependabot PR~~ — attributes the push to you, which causes Dependabot to "disavow" the PR and refuse future `@dependabot rebase` commands. Use `gh api -X PUT repos/OWNER/REPO/pulls/NUMBER/update-branch` instead
- ~~Manually merge a green Dependabot PR~~ — let the auto-merge workflow handle it. If it's not auto-merging, there's a bug in the system that manual merging will mask
- ~~Ship individual Dependabot PRs for interlocking ecosystems~~ (TanStack, React, Storybook, Vitest, SQLAlchemy stack, Pytest, Pydantic, FastAPI stack). Add a `groups:` entry to [.github/dependabot.yml](../.github/dependabot.yml) so they move atomically
- ~~Merge a Dependabot PR that only touches `package.json` / `pyproject.toml` without the corresponding lockfile update~~ — if the lockfile sync workflow is configured (Phase 5b), let it auto-fix. If not, close it, run the package manager's update command locally to regenerate both files, open a replacement PR
- ~~Use `GITHUB_TOKEN` to push from a workflow that needs CI to re-run on the new commit~~ — `GITHUB_TOKEN`-authored pushes do not trigger subsequent workflow runs, so CI will not fire on the pushed commit and the PR's required status checks will stay attached to the old, broken commit. Always use a fine-grained PAT (or a GitHub App installation token). This is the reason Phase 5b requires a PAT — see [automerge.md "Why a PAT is required"](automerge.md#why-a-pat-is-required-not-github_token)
- ~~Use a classic PAT when a fine-grained PAT will do~~ — fine-grained PATs are scoped to individual repos with individual permissions; classic PATs are account-wide and carry excessive scopes. For the lockfile sync workflow, the minimal fine-grained scopes are `Contents: Read and write` + `Pull requests: Read and write` on exactly the one repo that runs the workflow

---

## Why the order matters

Setup steps that look independent are actually a dependency graph:

1. **CodeQL before ruleset** → can't require a check that has never run
2. **Ruleset before first PR** → need the gates in place to test them
3. **Ruleset before `DEPENDABOT_AUTOMERGE_ENABLED=true`** → `gh pr merge --auto` waits only for checks declared on the ruleset; flipping the variable without the ruleset in place merges red PRs (Incident 1)
4. **Workflow permissions (Phase 5) before auto-merge variable (Phase 5a)** → the workflow's `gh pr merge --auto` call requires "Allow GitHub Actions to create and approve pull requests", silently fails without it
5. **Phase 5a before Phase 5b** → Phase 5b (lockfile sync) depends on the auto-merge workflow being armed, because the sync workflow's entire purpose is to unblock stuck Dependabot PRs so the auto-merge pipeline can complete. Enabling 5b without 5a leaves lockfile-fixed PRs sitting in `CLEAN` state waiting for someone to manually click merge
6. **First PR before CodeQL requirement** → CodeQL needs results before you can threshold on them
7. **Local env before rename** → can't verify renames work without running `task check`
8. **Rename before bootstrap PR** → the rename IS the bootstrap PR's content
9. **Sanity check last** → verifies everything above actually took effect

Trying to do these in any other order results in circular blockers. The order in this document is the minimal unblocking sequence.

---

## Troubleshooting Dependabot

Short reference. Full details, incident history, and architecture rationale in [automerge.md](automerge.md).

### "My Dependabot PR is green but not auto-merging"

Check in order:

1. **Is the PR author literally `dependabot[bot]`?** Any other bot author — `github-actions[bot]`, `renovate[bot]`, a personal PAT — falls outside the auto-merge exception. Manually squash-merge.
2. **Is `DEPENDABOT_AUTOMERGE_ENABLED` set to the literal string `"true"`?** Check with `gh variable list`. If empty, `"True"`, `"yes"`, or `"1"`, the workflow's `if:` evaluates false.
3. **Is the `main-protection` ruleset active with 4 required status checks bound to GitHub Actions?** Check with the verification command in [automerge.md, Verification commands](automerge.md#verification-commands). If the ruleset is missing or the checks aren't `integration_id: 15368`, auto-merge will NOT be safe — do not "fix" it by merging manually.
4. **Is the PR `BEHIND` main?** Strict mode requires the branch to be up to date. Use `gh api -X PUT repos/OWNER/REPO/pulls/NUMBER/update-branch` to rebase server-side. Do NOT click "Update branch" in the UI.
5. **Does the workflow file exist on main with the correct guard?** `gh api repos/OWNER/REPO/contents/.github/workflows/dependabot-automerge.yml --jq '.name'` should return `"dependabot-automerge.yml"`. The guard must read `github.event.pull_request.user.login`, not `github.actor`.

### "My Dependabot PR has red CI — `ERR_PNPM_OUTDATED_LOCKFILE` or `uv sync` error"

The lockfile-gap bug: Dependabot updated the manifest (`package.json` / `pyproject.toml`) without regenerating the lockfile (`pnpm-lock.yaml` / `uv.lock`). The template ships an automated fix — check it first.

**Check: did the `Dependabot lockfile sync` workflow run on this PR?**

Look at the Actions tab filtered to this PR. If the `Dependabot lockfile sync` job is listed and reports `conclusion: success` with a visible "Commit and push lockfile updates" step, the fix already happened — wait 30–60 seconds for the re-triggered CI to go green, then the auto-merge workflow will pick it up automatically.

If the sync job is **not** listed, or reports `conclusion: skipped`, or reports `conclusion: failure`, see the next section below.

**Manual fallback** (only if the sync workflow is not functioning):

**Frontend (pnpm):**
```bash
cd apps/frontend
pnpm update --latest <package>     # or pnpm update --latest for grouped updates
# commit both package.json and pnpm-lock.yaml atomically
```

**Backend (uv):**
```bash
cd apps/backend
uv sync --upgrade-package <package>
# commit both pyproject.toml and uv.lock atomically
```

Open a replacement PR. The auto-merge workflow will not fire on it (you're the PR author, not Dependabot) so squash-merge it manually.

### "The `Dependabot lockfile sync` workflow isn't fixing my PR"

Check in order:

1. **Is `DEPENDABOT_LOCKFILE_SYNC_ENABLED` set to the literal string `"true"`?** Check with `gh variable list`. Same pattern as the auto-merge variable — any other value (empty, `"True"`, `"yes"`) leaves the workflow disarmed.
2. **Is the `DEPENDABOT_LOCKFILE_SYNC_PAT` secret set?** Check with `gh secret list`. The first step of the sync workflow is "Verify PAT is configured" and it fails loudly with a specific error message directing you back to [Phase 5b](#phase-5b-arm-dependabot-lockfile-sync-pat--variable) if the secret is missing.
3. **Has the PAT expired?** Fine-grained PATs have an expiration date set at creation time. Check your [Developer settings → Personal access tokens → Fine-grained tokens](https://github.com/settings/personal-access-tokens) page. If the PAT expired, the workflow's checkout step fails with a `403 Bad credentials` error. Rotate the PAT and update the secret.
4. **Does the PAT have the right scopes?** Required: `Contents: Read and write` + `Pull requests: Read and write`, scoped to this repo. Insufficient scopes produce `403 Resource not accessible` errors in the checkout or push steps.
5. **Is the loop guard tripping unexpectedly?** The workflow's second step checks if the last commit was authored by `github-actions[bot]` and skips if so. Normally this only trips after the workflow's own previous run has already pushed a lockfile fix — which is correct behavior. If it's tripping on the first run, something weird is going on; look at the Actions run logs for the exact commit email the guard saw.

### "Dependabot says it has 'disavowed' my PR after I edited it"

You or a teammate clicked "Update branch" or pushed a commit to the PR branch. Dependabot's self-rebase refuses to operate on disavowed PRs; `@dependabot rebase` and `@dependabot recreate` are both ignored. The escape hatch is the server-side API, which is not owned by Dependabot:

```bash
gh api -X PUT repos/OWNER/REPO/pulls/NUMBER/update-branch
```

This works regardless of disavowal state. Avoid disavowal in the first place by using the same API instead of the UI button.

### "Multiple Dependabot PRs for the same ecosystem keep conflicting when I try to merge them sequentially"

Rebase-conflict cascade (Incident 3 in [automerge.md](automerge.md#incident-3-pr-16--rebase-conflict-cascade-on-sibling-pyprojecttoml-bumps)). Each merge modifies lines adjacent to the ones the next PR wants to change, and eventually git's 3-way merge gives up on the contextual resolution. Don't try to fight it.

Resolution: close the remaining PRs. Open a single manual replacement that bumps all the affected packages together. Then add a `groups:` entry to [.github/dependabot.yml](../.github/dependabot.yml) so the ecosystem ships as one atomic PR going forward. The repo's current groups are documented at the top of the file.

### "I need to disable auto-merge immediately"

One command:

```bash
gh variable set DEPENDABOT_AUTOMERGE_ENABLED --body "false"
```

Effective on the next `pull_request` event. The workflow still runs on every event but reports `skipped` instead of acting. Ruleset-based manual merging continues to work for humans. Re-enable later by setting the variable back to `"true"`.

**Do not** "disable" auto-merge by deleting the workflow, editing the ruleset, or removing branch protection. Those are heavier operations that touch multiple load-bearing pieces of the contract. The variable exists specifically as the emergency off switch.
