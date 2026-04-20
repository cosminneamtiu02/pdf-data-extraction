"""Static assertions on the Dockerfile HEALTHCHECK form (issue #363).

The original `HEALTHCHECK CMD python -c 'import urllib.request; ...'` was
shell-form and relied on `python` resolving via `ENV PATH="/app/.venv/bin:$PATH"`
to the venv interpreter. Every 30s that spawned a full cold Python
interpreter (~500MB of imports for a dependency-heavy venv: Docling,
LangExtract, PyMuPDF, torch CPU wheels) just to answer one HTTP request,
and under `docker stop` it could race against shutdown — producing a
mis-leading `unhealthy` flap right before the container exits.

The fix is to replace the `python -c` check with a non-Python probe
(`curl -fsS http://localhost:8000/health`) in exec form. Exec form is
required by the Docker reference as the "preferred" invocation because
it avoids wrapping the command in `/bin/sh -c`, and — more importantly
for this issue — curl is a ~250 KB static binary that does not touch
the app venv at all.

This test pins the fix so a future edit cannot silently regress the
HEALTHCHECK back to `python -c` or to shell form.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

# parents[5] walks: this file -> docker/ -> unit/ -> tests/ -> backend/ ->
# apps/ -> repo root. Mirrors the convention used by the sibling
# `test_dockerignore_at_repo_root` module.
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[5]
_DOCKERFILE_PATH: Final[Path] = _REPO_ROOT / "infra" / "docker" / "backend.Dockerfile"


def _read_dockerfile_text() -> str:
    """Return the Dockerfile contents, failing the calling test if missing.

    Using `pytest.fail(...)` keeps the error surface consistent with other
    Dockerfile guardrail tests even if a future refactor renames or moves
    the file: the diagnostic points directly at the expected path instead
    of letting `read_text()` raise a bare `FileNotFoundError`.
    """
    if not _DOCKERFILE_PATH.is_file():
        pytest.fail(
            f"expected Dockerfile at {_DOCKERFILE_PATH} — issue #363 guardrail "
            "cannot run if the backend Dockerfile has been moved or deleted."
        )
    return _DOCKERFILE_PATH.read_text(encoding="utf-8")


def _extract_healthcheck_cmd_line(text: str) -> str:
    """Return the `CMD ...` continuation line of the HEALTHCHECK instruction.

    The repo's HEALTHCHECK spans two physical lines (`HEALTHCHECK ...\\n CMD ...`)
    because line-continuations with `\\` produce the same single logical
    instruction to the Dockerfile parser. We normalise by first folding
    every `\\\n` sequence into a single space, then grepping the single
    logical HEALTHCHECK line out of the folded text and returning the
    portion that starts at `CMD`.
    """
    # Fold all backslash-newline continuations into a single space so the
    # whole HEALTHCHECK directive lives on one logical line, regardless of
    # how it was wrapped in the source file.
    folded = re.sub(r"\\\n\s*", " ", text)
    match = re.search(r"(?m)^HEALTHCHECK[^\n]*", folded)
    if match is None:
        pytest.fail(
            f"no HEALTHCHECK instruction found in {_DOCKERFILE_PATH}. Issue #363 "
            "expects the backend Dockerfile to declare a container-level "
            "liveness probe, even if it is later superseded by a compose-level "
            "healthcheck; dropping it entirely should be a deliberate decision "
            "with its own ADR."
        )
    instruction = match.group(0)
    cmd_idx = instruction.find("CMD")
    if cmd_idx < 0:
        pytest.fail(
            f"HEALTHCHECK instruction in {_DOCKERFILE_PATH} has no `CMD` clause: "
            f"{instruction!r}. The `NONE` form would disable healthchecks "
            "entirely — if that is what you want, update this guardrail and "
            "docs/decisions.md."
        )
    return instruction[cmd_idx:].strip()


def test_healthcheck_present() -> None:
    """A HEALTHCHECK instruction must exist in the Dockerfile.

    Docker treats a missing HEALTHCHECK as "status unknown," which
    orchestrators like compose/k8s will not gate deployments on. Issue
    #363 explicitly considered dropping the Dockerfile HEALTHCHECK in
    favour of the compose-level one, but decided to keep it so that
    `docker run` (without compose) still reports health.
    """
    text = _read_dockerfile_text()
    assert re.search(r"(?m)^HEALTHCHECK", text), (
        f"expected a `HEALTHCHECK` instruction in {_DOCKERFILE_PATH}; issue "
        "#363 keeps one at the image level so `docker run`-only deployments "
        "still report health status."
    )


def test_healthcheck_uses_exec_form() -> None:
    """HEALTHCHECK CMD must be in exec form (JSON array), not shell form.

    Shell form (`CMD python -c ...`) wraps the command in `/bin/sh -c`,
    which adds a shell fork and — in the original implementation —
    resolved `python` via `$PATH` to the venv interpreter. Exec form
    (`CMD ["curl", "-fsS", ...]`) invokes the binary directly with no
    shell. The Docker reference calls exec form the "preferred method."
    """
    cmd_line = _extract_healthcheck_cmd_line(_read_dockerfile_text())
    argv = cmd_line.removeprefix("CMD").strip()
    assert argv.startswith("["), (
        f"HEALTHCHECK CMD in {_DOCKERFILE_PATH} is shell form: {cmd_line!r}. "
        "Switch to exec form (JSON array): "
        '`CMD ["curl", "-fsS", "http://localhost:8000/health"]`. '
        "Issue #363."
    )


def test_healthcheck_does_not_invoke_python() -> None:
    """HEALTHCHECK must NOT spawn `python -c ...`.

    The load-bearing regression this guardrail prevents: using `python`
    as the probe binary means every 30s the daemon forks a full Python
    interpreter against the app venv. With Docling + LangExtract +
    PyMuPDF + torch CPU wheels on `sys.path`, that is a ~500MB cold
    import cost per probe, and it races against `docker stop` during
    graceful shutdown. Issue #363 replaces it with `curl`.

    This check looks specifically at the HEALTHCHECK line, not at the
    whole Dockerfile — a build-stage `RUN python -m ...` or an entrypoint
    that uses `python` is fine and out of scope here.
    """
    cmd_line = _extract_healthcheck_cmd_line(_read_dockerfile_text())
    assert "python" not in cmd_line, (
        f"HEALTHCHECK in {_DOCKERFILE_PATH} invokes Python: {cmd_line!r}. "
        "Use a non-Python probe (curl or wget) so the healthcheck does "
        "not pay the venv-import cost every 30s. Issue #363."
    )
