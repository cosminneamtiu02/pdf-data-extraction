"""Coverage wiring guardrail (issue #358).

Before this test, `apps/backend/pyproject.toml` pulled in ``pytest-cov`` in
the dev group and declared a ``[tool.coverage.report] fail_under = 80``
gate, but ``Taskfile.yml`` and ``.github/workflows/ci.yml`` both invoked
``pytest tests/unit/`` *without* any ``--cov=`` argument. Coverage was
therefore never collected and the ``fail_under`` gate was dormant — the
declared contract and the executed contract disagreed.

This test pins the agreed wiring:

1. The ``test:unit`` task in ``Taskfile.yml`` must invoke pytest with a
   ``--cov=app`` argument so coverage is collected against the production
   tree declared in ``[tool.coverage.run] source = ["app"]``.
2. The same task must carry a ``--cov-fail-under`` guard so the gate
   configured in ``[tool.coverage.report] fail_under`` is enforced at
   the Taskfile level (redundant with the pyproject default but explicit
   at the call site so a future refactor of ``pyproject.toml`` cannot
   silently drop the gate).
3. ``.github/workflows/ci.yml`` must mirror the local Taskfile: its
   ``Unit tests`` step must also run pytest with ``--cov=app`` so CI
   coverage matches local ``task check`` coverage. The mirror is
   load-bearing — if CI drifts to ``pytest tests/unit/ -v`` without
   ``--cov``, the gate silently stops firing on merge-blocking runs
   even while the local loop still enforces it.

These assertions use plain string-containment over the raw YAML so the
test survives ``yaml.safe_load`` collapsing multi-line ``run:`` blocks
or ``cmds:`` entries — the shell argv is what actually runs, and that
is what we check.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final, cast

import yaml

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[5]
_TASKFILE: Final[Path] = _REPO_ROOT / "Taskfile.yml"
_CI_WORKFLOW: Final[Path] = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_BACKEND_PYPROJECT: Final[Path] = _REPO_ROOT / "apps" / "backend" / "pyproject.toml"


def _load_taskfile() -> dict[str, Any]:
    raw = _TASKFILE.read_text(encoding="utf-8")
    return cast("dict[str, Any]", yaml.safe_load(raw))


def _load_ci_workflow() -> dict[str, Any]:
    raw = _CI_WORKFLOW.read_text(encoding="utf-8")
    return cast("dict[str, Any]", yaml.safe_load(raw))


def _test_unit_shell_cmds() -> list[str]:
    """Return the shell (non-``task:``) cmds of the ``test:unit`` Taskfile task."""
    task_def = _load_taskfile()["tasks"]["test:unit"]
    cmds = task_def.get("cmds") or []
    return [entry for entry in cmds if isinstance(entry, str)]


def test_test_unit_task_invokes_pytest_with_cov() -> None:
    """``task test:unit`` must pass ``--cov=app`` to pytest (issue #358).

    Without ``--cov=``, pytest-cov never hooks into the run and the
    ``fail_under`` gate declared in pyproject is dead config.
    """
    shell_cmds = _test_unit_shell_cmds()
    has_cov = any("--cov=app" in cmd for cmd in shell_cmds)
    assert has_cov, (
        "`task test:unit` must invoke pytest with `--cov=app` so coverage "
        "is collected and the `fail_under` gate in pyproject.toml actually "
        "fires. Current cmds: "
        f"{shell_cmds!r}. Issue #358."
    )


def test_test_unit_task_enforces_cov_fail_under() -> None:
    """``task test:unit`` must pass ``--cov-fail-under=<N>`` explicitly.

    Pyproject's ``[tool.coverage.report] fail_under`` is the default
    applied by ``coverage report``, but passing ``--cov-fail-under`` at
    the call site makes the gate visible in the Taskfile and resilient
    to a future refactor that drops the pyproject key. This is the same
    explicit-at-the-call-site posture already used for ``-m "not slow"``
    on ``task test:integration``.
    """
    shell_cmds = _test_unit_shell_cmds()
    has_gate = any("--cov-fail-under" in cmd for cmd in shell_cmds)
    assert has_gate, (
        "`task test:unit` must invoke pytest with an explicit "
        "`--cov-fail-under=<N>` argument so the coverage gate is visible "
        "at the call site, not only in pyproject.toml. Current cmds: "
        f"{shell_cmds!r}. Issue #358."
    )


def test_ci_unit_tests_step_invokes_pytest_with_cov() -> None:
    """``.github/workflows/ci.yml`` Unit tests step must mirror the local gate.

    The CI path is the merge-blocking one; if it drifts to plain
    ``pytest tests/unit/ -v`` while the Taskfile keeps ``--cov=``, the
    gate silently stops firing on PRs.
    """
    workflow = _load_ci_workflow()
    jobs: dict[str, Any] = workflow.get("jobs") or {}
    unit_test_run_blocks: list[str] = []
    for job_body in jobs.values():
        for step in job_body.get("steps") or []:
            step_name = step.get("name", "")
            run_body = step.get("run", "")
            if step_name == "Unit tests" and isinstance(run_body, str):
                unit_test_run_blocks.append(run_body)

    assert unit_test_run_blocks, (
        "ci.yml has no step named 'Unit tests'; the coverage wiring "
        "mirror cannot be verified. Issue #358."
    )
    has_cov = any("--cov=app" in block for block in unit_test_run_blocks)
    assert has_cov, (
        "ci.yml 'Unit tests' step must invoke pytest with `--cov=app` so "
        "coverage is collected on PRs (mirror of `task test:unit` wiring). "
        f"Current run blocks: {unit_test_run_blocks!r}. Issue #358."
    )


def test_pyproject_declares_coverage_fail_under() -> None:
    """``[tool.coverage.report] fail_under`` must stay declared in pyproject.

    The ``--cov-fail-under=<N>`` argument on the Taskfile line is the
    visible gate; pyproject's ``fail_under`` is the default applied when
    the CLI omits it (e.g. ``coverage report`` standalone). Keep both in
    sync so either invocation path enforces the same threshold.
    """
    import tomllib

    data = tomllib.loads(_BACKEND_PYPROJECT.read_text(encoding="utf-8"))
    cov_report = (data.get("tool") or {}).get("coverage", {}).get("report", {})
    assert "fail_under" in cov_report, (
        "[tool.coverage.report] must declare `fail_under` so pyproject "
        "and the Taskfile remain the agreed source of truth for the "
        "coverage gate. Issue #358."
    )
