"""Meta-tests for the `error-contracts` job-level concurrency block in `.github/workflows/ci.yml`.

Issue #410: pin the shape of the job-level `concurrency` block on the
`error-contracts` job so future edits cannot silently drop it or rename it
into a group that collides with another workflow's concurrency group.

What this block DOES enforce (and what this test pins):

1. `error-contracts` declares its own job-level `concurrency` mapping,
   separate from the workflow-level block at the top of `ci.yml`.
2. The group name is prefixed with `${{ github.workflow }}` so it cannot
   collide with similarly named groups in other workflows in this repo
   (e.g. `deploy.yml`, `dependabot-*.yml`). `concurrency.group` names are
   repository-global and case-insensitive per GitHub's docs, so a prefix
   is the only way to guarantee cross-workflow isolation.
3. The group includes `${{ github.ref }}` so concurrent runs on different
   branches/PRs do not cancel each other.
4. `cancel-in-progress: true` is pinned at the job level so this job's
   cancellation semantics are documented locally and cannot be weakened
   by a future change to the workflow-level block alone.

What this block does NOT do (and what this test does NOT claim):

The workflow-level `concurrency` block at the top of `ci.yml` still uses
`cancel-in-progress: true`, which cancels the ENTIRE prior workflow run
(including `error-contracts`) on stacked pushes to the same ref. Job-level
concurrency layers on top of workflow-level concurrency — it does not
override or replace it. Preventing stacked-push cancellation of
`error-contracts` specifically would require a separate change to the
workflow-level block, which is out of scope for #410.

If a future edit removes the job-level `concurrency` block, drops
`cancel-in-progress: true`, or drops the `${{ github.workflow }}` prefix
from the group, CI fails loudly here with a pointer back to #410.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import yaml

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[5]
_CI_WORKFLOW_PATH: Final[Path] = _REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _load_ci_workflow() -> dict[str, Any]:
    data = yaml.safe_load(_CI_WORKFLOW_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = (
            f"{_CI_WORKFLOW_PATH} did not parse to a mapping "
            f"(got {type(data).__name__}); workflow schema may have changed."
        )
        raise AssertionError(msg)  # noqa: TRY004
    return data


def _get_job(name: str) -> dict[str, Any]:
    workflow = _load_ci_workflow()
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        msg = (
            f"{_CI_WORKFLOW_PATH} top-level 'jobs' is {type(jobs).__name__!r} "
            f"(expected mapping); workflow schema may have changed."
        )
        raise AssertionError(msg)  # noqa: TRY004
    job = jobs.get(name)
    if not isinstance(job, dict):
        msg = (
            f"Job {name!r} missing from {_CI_WORKFLOW_PATH} or not a mapping "
            f"(got {type(job).__name__}). Available jobs: {sorted(jobs)}."
        )
        raise AssertionError(msg)  # noqa: TRY004
    return job


def test_error_contracts_job_has_concurrency_block() -> None:
    """The `error-contracts` job must declare a job-level `concurrency` block.

    The block pins cross-workflow isolation (group prefix) and
    `cancel-in-progress` semantics locally so a future edit to the
    workflow-level block cannot silently weaken them. See #410 for the
    scope of what this block does and does not address.
    """
    job = _get_job("error-contracts")
    concurrency = job.get("concurrency")
    assert isinstance(concurrency, dict), (
        "error-contracts job is missing a `concurrency:` mapping in "
        f"{_CI_WORKFLOW_PATH}. Add a job-level block scoped to this job "
        "(e.g. group: ${{ github.workflow }}-ci-error-contracts-${{ github.ref }}, "
        "cancel-in-progress: true) so error-contracts has its own "
        "job-scoped concurrency policy isolated from the workflow-level "
        "group. See issue #410."
    )


def test_error_contracts_concurrency_group_is_job_scoped() -> None:
    """The job-level `group` must be distinct from the workflow-level group.

    Using `${{ github.workflow }}-${{ github.ref }}` at the job level would
    collide with the workflow-level group and give no isolation. The group
    string must identify the `error-contracts` job specifically, and must
    include `${{ github.workflow }}` so the repo-global group namespace
    cannot collide with similarly named groups in other workflows.
    """
    job = _get_job("error-contracts")
    concurrency = job.get("concurrency")
    assert isinstance(concurrency, dict)
    group = concurrency.get("group")
    assert isinstance(group, str), (
        f"error-contracts job's concurrency.group must be a string (got {type(group).__name__})."
    )
    assert group, "error-contracts job's concurrency.group must be non-empty."
    assert "error-contracts" in group, (
        f"error-contracts job's concurrency.group is {group!r}, which does "
        "not contain 'error-contracts'. Use a job-scoped identifier such as "
        "`${{ github.workflow }}-ci-error-contracts-${{ github.ref }}` so "
        "the group does not collide with the workflow-level group. See "
        "issue #410."
    )
    assert "github.ref" in group, (
        f"error-contracts job's concurrency.group is {group!r}, which does "
        "not include `${{ github.ref }}`. Scope the group per-ref so "
        "concurrent runs on different branches/PRs do not cancel each "
        "other. See issue #410."
    )
    assert "github.workflow" in group, (
        f"error-contracts job's concurrency.group is {group!r}, which does "
        "not include `${{ github.workflow }}`. `concurrency.group` names "
        "are repository-global, so prefix the group with "
        "`${{ github.workflow }}` (matching the workflow-level group and "
        "other workflows in this repo such as deploy.yml, "
        "dependabot-*.yml) to avoid cross-workflow collisions. See "
        "issue #410."
    )


def test_error_contracts_concurrency_cancels_in_progress() -> None:
    """`cancel-in-progress: true` at the job level is required.

    Without it, a second push to the same ref would queue behind the
    stale error-contracts run instead of cancelling it within this
    job-scoped group, wasting runner minutes for the specific case where
    the workflow-level cancellation did not apply (e.g. a future edit
    that removes or scopes the workflow-level block differently). See
    issue #410.
    """
    job = _get_job("error-contracts")
    concurrency = job.get("concurrency")
    assert isinstance(concurrency, dict)
    cancel = concurrency.get("cancel-in-progress")
    assert cancel is True, (
        "error-contracts job's concurrency.cancel-in-progress must be "
        f"literally `true` (got {cancel!r}). Without it, stacked pushes "
        "queue instead of cancelling in this job-scoped group, defeating "
        "the point of the job-level concurrency block. See issue #410."
    )
