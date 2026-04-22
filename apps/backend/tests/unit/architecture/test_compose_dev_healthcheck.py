"""Static assertions on the dev compose file's healthcheck (issue #407).

The dev compose file at `infra/compose/docker-compose.yml` bind-mounts
`apps/backend/app` over the baked image path `/app/app` so edits hot-
reload inside the container. That overlay means a green image build is
not sufficient — a broken host `.py` file is enough to make uvicorn
refuse to boot inside the container while `docker compose up` still
reports the container as `running` (because without a HEALTHCHECK,
Docker has no way to mark it `unhealthy`).

Issue #407 adds a compose-level HEALTHCHECK to the dev file so the
container's true state surfaces. The probe points at `/health` rather
than `/ready`: dev workflows rarely have Ollama running, and `/ready`
returns 503 `ollama_unreachable` in that case, which would falsely
flag the dev container as unhealthy. `/health` is a pure liveness
signal (the process is answering HTTP) and does not reach out to
Ollama, so it stays green in a no-Ollama dev loop.

This test pins the fix so a future edit cannot silently regress the
dev compose back to "no healthcheck" or to pointing at `/ready`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import pytest
import yaml

from ._linter_subprocess import REPO_ROOT

_DEV_COMPOSE: Final[Path] = REPO_ROOT / "infra" / "compose" / "docker-compose.yml"


def _load_dev_compose() -> dict[str, Any]:
    """Return the parsed dev compose document, failing loudly if missing.

    Uses `pytest.fail` (which raises the internal `Failed` exception) rather
    than a bare `raise AssertionError`, so a type-mismatched YAML structure
    shows up as a test failure without tripping ruff's `TRY004` rule about
    using `TypeError` for type validation. The semantics match: either way
    the test stops cold and pytest reports it as failed.
    """
    if not _DEV_COMPOSE.is_file():
        pytest.fail(
            f"expected dev compose file at {_DEV_COMPOSE} — issue #407 "
            "guardrail cannot run if the dev compose has been moved or "
            "deleted."
        )
    data = yaml.safe_load(_DEV_COMPOSE.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        pytest.fail(
            f"dev compose at {_DEV_COMPOSE} did not parse to a mapping "
            f"(got {type(data).__name__!r}). A valid compose file is always "
            "a YAML mapping at the top level."
        )
    return data


def _backend_service() -> dict[str, Any]:
    """Return the `backend` service mapping from the dev compose file."""
    compose = _load_dev_compose()
    services = compose.get("services")
    if not isinstance(services, dict):
        pytest.fail(
            f"dev compose at {_DEV_COMPOSE} has no `services:` mapping. "
            "Every compose file declares its containers under `services:`."
        )
    backend = services.get("backend")
    if not isinstance(backend, dict):
        pytest.fail(
            f"dev compose at {_DEV_COMPOSE} has no `backend` service "
            "mapping. Issue #407 guards the backend container's healthcheck; "
            "if the service was renamed, update this guardrail in lockstep."
        )
    return backend


def test_dev_compose_backend_declares_healthcheck() -> None:
    """The dev compose backend service must declare a `healthcheck:` block.

    Without a compose-level healthcheck, `docker compose up` reports
    `running` for the dev container even when the bind-mounted app tree
    causes uvicorn to crash on boot. Issue #407.
    """
    backend = _backend_service()
    assert "healthcheck" in backend, (
        f"`backend` service in {_DEV_COMPOSE} has no `healthcheck:` block. "
        "Issue #407: the dev bind-mount of apps/backend/app → /app/app "
        "means a working image plus a broken host file leaves the "
        "container `running` but uvicorn dead; a healthcheck is the only "
        "way Docker can surface that state."
    )


def test_dev_compose_healthcheck_targets_health_not_ready() -> None:
    """The dev compose healthcheck must probe `/health`, not `/ready`.

    `/ready` reaches out to Ollama; in a typical dev loop Ollama is not
    running, so `/ready` returns 503 `ollama_unreachable` and the dev
    container would flap to `unhealthy`. `/health` is a pure in-process
    liveness signal and stays green without Ollama. Issue #407.
    """
    backend = _backend_service()
    healthcheck = backend.get("healthcheck")
    assert isinstance(healthcheck, dict), (
        f"`backend.healthcheck` in {_DEV_COMPOSE} is not a mapping "
        f"(got {type(healthcheck).__name__!r}). Compose requires a mapping "
        "with at least a `test:` key."
    )
    test_value = healthcheck.get("test")
    # `test:` is either a string (shell form) or a list ([CMD, ...] /
    # [CMD-SHELL, ...] — exec form). Serialize to a single searchable
    # string either way; the endpoint path is what we care about.
    if isinstance(test_value, list):
        flattened = " ".join(str(part) for part in test_value)
    elif isinstance(test_value, str):
        flattened = test_value
    else:
        pytest.fail(
            f"`backend.healthcheck.test` in {_DEV_COMPOSE} is neither a "
            f"string nor a list (got {type(test_value).__name__!r}). "
            "Compose's healthcheck schema only accepts those two shapes."
        )
    assert "/health" in flattened, (
        f"`backend.healthcheck.test` in {_DEV_COMPOSE} does not probe "
        f"`/health`: {test_value!r}. Issue #407: dev loops often run "
        "without Ollama, which makes `/ready` return 503 and falsely flap "
        "the container to `unhealthy`. Use `/health` for pure liveness."
    )
    assert "/ready" not in flattened, (
        f"`backend.healthcheck.test` in {_DEV_COMPOSE} probes `/ready`: "
        f"{test_value!r}. Issue #407 specifically swapped this out for "
        "`/health` because `/ready` requires Ollama, which dev loops "
        "typically do not run. If `/ready` is intentionally desired in "
        "dev, update this guardrail and docs/decisions.md with rationale."
    )
