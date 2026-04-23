"""Hygiene checks on `.github/workflows/ci.yml`.

Static assertions about CI workflow steps — kept in this infra hygiene
tree (issue #400) so they live next to the workflows they assert on
rather than inside the backend unit-test tree. `task check` runs them
via the `check:hygiene` gate. Catches drift of hardening invariants the
repo has agreed to across PRs (#190 SHA-pinning, #212 persist-credentials
narrowing, etc.) so a future copy-paste does not silently regress the
posture.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import yaml

from ._paths import REPO_ROOT

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

    The assertion enforces THREE things that together guarantee "every PR
    builds the Dockerfile" (PR #418 review):

    1. The workflow fires on the ``pull_request`` event with no ``paths:``
       filter that could silently skip PRs touching files outside a
       narrow subset.
    2. At least one step in some job invokes ``docker build`` against
       the repo-root backend Dockerfile.
    3. Neither that step nor its containing job carries an ``if:`` guard
       that could skip the build for some subset of PRs (e.g. a future
       ``if: vars.DOCKER_BUILD_ENABLED == 'true'`` would make the gate
       toggleable, which is the exact failure mode this test guards
       against).

    Job and step names are intentionally not constrained so the test
    doesn't lock in naming invariants.
    """
    workflow: dict[str, Any] = yaml.safe_load(_CI_WORKFLOW.read_text())

    # (1) pull_request trigger with no paths-filter gating.
    # PyYAML parses YAML's bare ``on:`` key as the Python literal ``True``
    # because ``on`` is a YAML 1.1 boolean. The workflow still fires
    # correctly on GitHub (it interprets the string ``on:``), but our
    # walker has to look under both keys. The ``# type: ignore`` is
    # load-bearing: ``dict.get`` is typed as ``(str) -> V``, and we're
    # intentionally probing a bool key that arises from YAML 1.1
    # parsing of the literal ``on`` token.
    triggers: dict[str, Any] = (
        workflow.get("on") or workflow.get(True)  # type: ignore[arg-type]  # YAML 1.1 parses `on:` as bool True
    ) or {}
    pull_request_config = triggers.get("pull_request")
    assert pull_request_config is not None, (
        "ci.yml does not declare a `pull_request` trigger; the "
        "dockerfile-build gate cannot fire on every PR."
    )
    if isinstance(pull_request_config, dict):
        paths_filter = pull_request_config.get("paths")
        paths_ignore_filter = pull_request_config.get("paths-ignore")
        assert paths_filter is None, (
            "ci.yml `pull_request` trigger declares a `paths:` filter "
            f"({paths_filter!r}); PRs outside that filter would skip the "
            "dockerfile-build gate. Remove the filter or switch to a "
            "non-gating always-on trigger."
        )
        assert paths_ignore_filter is None, (
            "ci.yml `pull_request` trigger declares a `paths-ignore:` "
            f"filter ({paths_ignore_filter!r}); PRs matching those paths "
            "would skip the dockerfile-build gate."
        )

    # (2) Locate the matching step AND its job body (for the if:
    # assertion below).
    jobs: dict[str, Any] = workflow.get("jobs") or {}
    matching: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for job_name, job_body in jobs.items():
        for step in job_body.get("steps") or []:
            run_body = step.get("run")
            if not isinstance(run_body, str):
                continue
            if "docker build" in run_body and _BACKEND_DOCKERFILE_RELATIVE in run_body:
                matching.append((job_name, job_body, step))

    assert matching, (
        f"ci.yml has no step that runs `docker build` against "
        f"`{_BACKEND_DOCKERFILE_RELATIVE}`. Without this gate a broken "
        f"Dockerfile can ship to main undetected because the deploy "
        f"workflow's job-level DEPLOY_ENABLED gate skips the Build step "
        f"(issue #417)."
    )

    # (3) Neither the matching step nor its job carries an `if:` guard.
    # At least one matching (job, step) pair must be unconditional; we
    # don't require ALL matches to be unconditional in case the file
    # legitimately has other gated docker-build steps later (e.g. a
    # slow/opt-in variant).
    unconditional = [
        (job_name, step)
        for job_name, job_body, step in matching
        if "if" not in job_body and "if" not in step
    ]
    assert unconditional, (
        "Every matching `docker build` step in ci.yml is gated with an "
        "`if:` clause at either the job or step level. A conditional "
        "dockerfile-build gate defeats the whole point of #417 — if the "
        "gate is toggleable, a broken Dockerfile can still slip through "
        "whenever the toggle is off. Keep at least one unconditional "
        "docker-build step that runs on every PR."
    )
