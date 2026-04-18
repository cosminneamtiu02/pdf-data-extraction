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
