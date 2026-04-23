"""Workflow-file hygiene checks that apply to every Dependabot CI path.

Static assertions about `.github/workflows/*.yml` that catch drift in the
rules CLAUDE.md codifies for Dependabot handling. Kept in this infra
hygiene tree (issue #400) so they live next to the workflows they assert
on rather than inside the backend unit-test tree. `task check` runs them
via the `check:hygiene` gate rather than relying on GitHub-side linting.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import pytest
import yaml

from ._paths import REPO_ROOT

_WORKFLOWS_DIR: Final[Path] = REPO_ROOT / ".github" / "workflows"

# Dependabot workflows that act on an individual PR. Every one of these must
# be serialised by PR number so two events on the same PR cannot race — see
# issue #209 for the incident-flavoured rationale.
_DEPENDABOT_PR_WORKFLOWS: Final[tuple[str, ...]] = (
    "dependabot-automerge.yml",
    "dependabot-lockfile-sync.yml",
)


@pytest.mark.parametrize("workflow_name", _DEPENDABOT_PR_WORKFLOWS)
def test_dependabot_pr_workflow_declares_concurrency_block(workflow_name: str) -> None:
    """Each Dependabot-PR workflow must serialise runs per PR number.

    Without a ``concurrency:`` block scoped to ``pull_request.number``, two
    events firing close together on the same PR (a ``synchronize`` from a
    lockfile-sync push plus a ``synchronize`` from a server-side
    ``update-branch`` call, for example) produce two competing workflow
    runs. ``cancel-in-progress`` is deliberately false — a partial run must
    finish rather than be killed mid-way.
    """
    workflow_path = _WORKFLOWS_DIR / workflow_name
    workflow: dict[str, Any] = yaml.safe_load(workflow_path.read_text())

    assert isinstance(workflow, dict), (
        f"{workflow_path} did not parse to a YAML mapping — "
        "got a non-mapping or null value, likely malformed YAML."
    )

    assert "concurrency" in workflow, (
        f"{workflow_name} lacks a top-level `concurrency:` block. "
        "Two events firing close together on the same Dependabot PR could race."
    )

    concurrency_block = workflow["concurrency"]
    assert isinstance(concurrency_block, dict), (
        f"{workflow_name}'s `concurrency:` must be a mapping with `group` and `cancel-in-progress`."
    )

    group = concurrency_block.get("group", "")
    # Assert the FULL ``${{ ... }}`` interpolation, not just a substring.
    # A bare ``group: github.event.pull_request.number`` without the
    # ``${{ }}`` wrapping is a literal string that would serialise ALL PRs
    # into one shared group rather than per-PR — the substring match would
    # let that through. Require the interpolation syntax AND
    # ``${{ github.workflow }}`` so cross-workflow collisions cannot share
    # the same group either.
    assert "${{ github.event.pull_request.number }}" in group, (
        f"{workflow_name}'s concurrency group must interpolate "
        "`${{ github.event.pull_request.number }}` (the full GitHub "
        "Actions expression, including `${{ }}`) so runs are serialised "
        f"per PR and not as a shared literal. Got: {group!r}"
    )
    assert "${{ github.workflow }}" in group, (
        f"{workflow_name}'s concurrency group must also interpolate "
        "`${{ github.workflow }}` so different workflows on the same PR "
        f"use distinct groups and do not cross-collide. Got: {group!r}"
    )

    cancel_in_progress = concurrency_block.get("cancel-in-progress")
    assert cancel_in_progress is False, (
        f"{workflow_name}'s `cancel-in-progress` must be false — a partial "
        "run (e.g. a lockfile-sync mid-push) must complete rather than be "
        f"killed. Got: {cancel_in_progress!r}"
    )
