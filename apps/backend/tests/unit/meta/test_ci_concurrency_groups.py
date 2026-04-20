"""Meta-tests for job-level concurrency groups in `.github/workflows/ci.yml`.

Issue #410: the `error-contracts` job shared the workflow-level concurrency
group `${{ github.workflow }}-${{ github.ref }}` (with
`cancel-in-progress: true`). Stacked pushes to the same branch therefore
cancelled in-flight error-contracts runs alongside the long `backend-checks`
job. A cancelled status check does NOT satisfy the ruleset's
required-status-checks gate: cancelled shows as "missing" to
`gh pr merge --auto`, which then waits forever.

The fix is to add a job-level `concurrency` block to the `error-contracts`
job so its own short window (~1 min) is scoped independently of the
workflow-level group, i.e. a second push cancels only the stale
error-contracts run, not the whole workflow, and the fresh error-contracts
run completes without getting cancelled on the next push while the long job
is still finishing.

This test pins the invariant: if a future edit removes the job-level
`concurrency` block or drops `cancel-in-progress: true`, CI fails loudly
with a pointer back to #410.
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

    Without it, the job inherits only the workflow-level group and gets
    cancelled alongside `backend-checks` on every stacked push — see #410.
    """
    job = _get_job("error-contracts")
    concurrency = job.get("concurrency")
    assert isinstance(concurrency, dict), (
        "error-contracts job is missing a `concurrency:` mapping in "
        f"{_CI_WORKFLOW_PATH}. Add a job-level block scoped to this job "
        "(e.g. group: ci-error-contracts-${{ github.ref }}, "
        "cancel-in-progress: true) so stacked pushes cancel only the "
        "stale error-contracts run, not the full workflow. See issue #410."
    )


def test_error_contracts_concurrency_group_is_job_scoped() -> None:
    """The job-level `group` must be distinct from the workflow-level group.

    Using `${{ github.workflow }}-${{ github.ref }}` at the job level would
    collide with the workflow-level group and give no isolation. The group
    string must identify the `error-contracts` job specifically.
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
        "`ci-error-contracts-${{ github.ref }}` so the group does not "
        "collide with the workflow-level group. See issue #410."
    )
    assert "github.ref" in group, (
        f"error-contracts job's concurrency.group is {group!r}, which does "
        "not include `${{ github.ref }}`. Scope the group per-ref so "
        "concurrent runs on different branches/PRs do not cancel each "
        "other. See issue #410."
    )


def test_error_contracts_concurrency_cancels_in_progress() -> None:
    """`cancel-in-progress: true` at the job level is required.

    Without it, a second push to the same ref would queue behind the
    stale error-contracts run instead of cancelling it, wasting runner
    minutes (the core complaint of #410).
    """
    job = _get_job("error-contracts")
    concurrency = job.get("concurrency")
    assert isinstance(concurrency, dict)
    cancel = concurrency.get("cancel-in-progress")
    assert cancel is True, (
        "error-contracts job's concurrency.cancel-in-progress must be "
        f"literally `true` (got {cancel!r}). Without it, stacked pushes "
        "queue instead of cancelling, defeating the point of the "
        "job-level concurrency group. See issue #410."
    )
