"""Hygiene checks on `.github/workflows/deploy.yml`.

Static assertions about the Deploy workflow — kept here so they run inside
the canonical `task check` gate. Catches drift of the
"do not habituate on-call to a red Deploy job" invariant (#270): until the
container-registry push flow from #121 is wired up, the Deploy job must be
GATED on a repo variable (``DEPLOY_ENABLED``) so GitHub Actions shows it
as **skipped** (neutral) rather than **failed** (red) on every push to main.

When #121 lands, the operator flips the repo variable to ``"true"`` and
the existing job runs. No workflow edit should be required for the flip.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import yaml

from ._linter_subprocess import REPO_ROOT

_DEPLOY_WORKFLOW: Final[Path] = REPO_ROOT / ".github" / "workflows" / "deploy.yml"


def _load_deploy_workflow() -> dict[str, Any]:
    workflow = yaml.safe_load(_DEPLOY_WORKFLOW.read_text())
    assert isinstance(workflow, dict), (
        f"{_DEPLOY_WORKFLOW} did not parse to a YAML mapping — "
        "got a non-mapping or null value, likely malformed YAML."
    )
    return workflow


def test_deploy_job_is_gated_on_deploy_enabled_variable() -> None:
    """The `deploy` job must be gated on the ``DEPLOY_ENABLED`` repo variable.

    Until the rollout story from #121 is wired up, the Deploy workflow has
    nothing meaningful to do — but emitting a red failure on every push to
    main habituates on-call to ignore the Deploy job, which hides real
    failures once the registry push is wired up. The kill switch is at the
    job level (``if: vars.DEPLOY_ENABLED == 'true'``) so GitHub Actions
    renders the whole job as "skipped" (neutral grey) rather than "failed"
    (red) while the variable is unset or anything other than the string
    ``"true"``. Operators flip it to ``"true"`` when the registry path is
    ready; no workflow edit needed.
    """
    workflow = _load_deploy_workflow()
    deploy_job = (workflow.get("jobs") or {}).get("deploy")

    assert deploy_job is not None, (
        "deploy.yml must define a `deploy` job — the kill-switch gate "
        "cannot be enforced against a missing job."
    )

    assert isinstance(deploy_job, dict), (
        f"`deploy` job must be a YAML mapping; got {type(deploy_job).__name__}."
    )

    guard = deploy_job.get("if")
    assert guard == "vars.DEPLOY_ENABLED == 'true'", (
        "The `deploy` job must carry `if: vars.DEPLOY_ENABLED == 'true'` "
        "so it is SKIPPED (neutral) rather than FAILED (red) on every push "
        "to main until the registry-push path from #121 is wired up. "
        f"Got: {guard!r}"
    )


def test_deploy_job_has_no_unconditional_exit_one() -> None:
    """No step in the `deploy` job may carry a raw `exit 1`.

    The pre-#270 workflow shipped a ``Push to registry`` step whose body
    was ``echo "::error::..."; exit 1`` — a permanent red annotation that
    fired on every push to main. Gating the job at the ``if:`` level is
    not enough on its own: once the variable flips to ``"true"`` and the
    job runs, a forgotten ``exit 1`` in the body would reintroduce the
    red. Keep the body a descriptive placeholder that exits cleanly until
    the real registry push replaces it.
    """
    workflow = _load_deploy_workflow()
    deploy_job = (workflow.get("jobs") or {}).get("deploy")
    assert isinstance(deploy_job, dict), "deploy job missing or malformed."

    offenders: list[str] = []
    for step in deploy_job.get("steps") or []:
        run_block = step.get("run")
        if not isinstance(run_block, str):
            continue
        # Check every non-blank, non-comment line for a bare `exit 1`.
        for raw_line in run_block.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            # Match `exit 1` as its own statement OR at the end of a
            # compound statement (`foo && exit 1`). Do not over-match on
            # `exit 10` etc.
            tokens = line.replace(";", " ").replace("&&", " ").split()
            for idx, tok in enumerate(tokens):
                if tok == "exit" and idx + 1 < len(tokens) and tokens[idx + 1] == "1":
                    offenders.append(
                        f"step '{step.get('name', '<unnamed>')}' contains `exit 1`: {line!r}",
                    )
                    break

    assert not offenders, (
        "deploy.yml `deploy` job steps must not unconditionally `exit 1` — "
        "that reintroduces the permanent-red regression tracked in #270:\n"
        + "\n".join(f"  - {o}" for o in offenders)
    )
