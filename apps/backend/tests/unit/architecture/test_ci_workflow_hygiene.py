"""Hygiene checks on `.github/workflows/ci.yml`.

Static assertions about CI workflow steps — kept in the backend test tree
so they run inside the canonical `task check` gate. Catches drift of
hardening invariants the repo has agreed to across PRs (#190 SHA-pinning,
#212 persist-credentials narrowing, etc.) so a future copy-paste does not
silently regress the posture.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import yaml

from ._linter_subprocess import REPO_ROOT

_CI_WORKFLOW: Final[Path] = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _iter_steps(workflow: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Return (job_name, step_dict) pairs for every step in a workflow document."""
    return [
        (job_name, step)
        for job_name, job_body in (workflow.get("jobs") or {}).items()
        for step in job_body.get("steps") or []
    ]


def test_ci_checkout_steps_disable_credential_persistence() -> None:
    """Every `actions/checkout` step in ci.yml must set persist-credentials: false.

    The checkout action's default persists the ephemeral ``GITHUB_TOKEN``
    into the job's git config for the lifetime of the job. Neither CI job
    here pushes back to the repo, so leaving the token live is unnecessary
    attack surface — a compromised `run:` step inherits write access to
    the repo. Continuation of the supply-chain hardening done in #190.
    """
    workflow: dict[str, Any] = yaml.safe_load(_CI_WORKFLOW.read_text())

    offenders: list[str] = []
    for job_name, step in _iter_steps(workflow):
        uses = step.get("uses", "")
        if not uses.startswith("actions/checkout@"):
            continue
        with_block = step.get("with") or {}
        if with_block.get("persist-credentials") is not False:
            offenders.append(
                f"job '{job_name}' step '{step.get('name', uses)}' "
                f"has persist-credentials={with_block.get('persist-credentials')!r}",
            )

    assert not offenders, (
        "ci.yml checkout step(s) leave the GITHUB_TOKEN in git config:\n"
        + "\n".join(f"  - {o}" for o in offenders)
    )


_BACKEND_DOCKERFILE_RELATIVE = "infra/docker/backend.Dockerfile"


def test_ci_builds_dockerfile_on_every_pr() -> None:
    """`ci.yml` must build ``infra/docker/backend.Dockerfile`` on every PR.

    Issue #417: the deploy workflow's job-level ``if: vars.DEPLOY_ENABLED
    == 'true'`` gate (landed via #300) causes the whole ``deploy`` job —
    including the Build step — to be skipped on every merge to ``main``.
    ``ci.yml`` does not build the Dockerfile either. The static Dockerfile
    hygiene checks cannot catch runtime-of-build failures (wrong COPY
    paths, apt package disappeared, etc.), so a broken Dockerfile can
    sit on ``main`` until someone flips ``DEPLOY_ENABLED=true``.

    This test pins the gate: some job in ``ci.yml`` must invoke
    ``docker build`` against the repo-root backend Dockerfile. The job
    name is not constrained (implementers can pick what fits the layout)
    so the test doesn't lock in an unnecessary naming invariant.
    """
    workflow: dict[str, Any] = yaml.safe_load(_CI_WORKFLOW.read_text())

    matching_steps: list[tuple[str, str]] = []
    for job_name, step in _iter_steps(workflow):
        run_body = step.get("run")
        if not isinstance(run_body, str):
            continue
        if "docker build" in run_body and _BACKEND_DOCKERFILE_RELATIVE in run_body:
            matching_steps.append((job_name, step.get("name", "")))

    assert matching_steps, (
        f"ci.yml has no step that runs `docker build` against "
        f"`{_BACKEND_DOCKERFILE_RELATIVE}`. Without this gate a broken "
        f"Dockerfile can ship to main undetected because the deploy "
        f"workflow's job-level DEPLOY_ENABLED gate skips the Build step "
        f"(issue #417)."
    )
