"""Static assertions on the Dockerfile HEALTHCHECK form (issue #363).

The original `HEALTHCHECK CMD python -c 'import urllib.request; ...'` was
shell-form and relied on `python` resolving via `ENV PATH="/app/.venv/bin:$PATH"`
to the venv interpreter. Every 30s that spawned a full cold Python
interpreter (~500MB of imports for a dependency-heavy venv: Docling,
LangExtract, PyMuPDF, torch CPU wheels) just to answer one HTTP request,
and under `docker stop` it could race against shutdown — producing a
misleading `unhealthy` flap right before the container exits.

The fix is to replace the `python -c` check with a non-Python probe
(`curl -fsS --output /dev/null http://localhost:8000/health`) in exec
form. Exec form is required by the Docker reference as the "preferred"
invocation because it avoids wrapping the command in `/bin/sh -c`, and —
more importantly for this issue — curl is a small system binary that
probes HTTP without invoking Python or touching the app venv at all.

This test pins the fix so a future edit cannot silently regress the
HEALTHCHECK back to `python -c` or to shell form.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Final

# parents[5] walks: this file -> docker/ -> unit/ -> tests/ -> backend/ ->
# apps/ -> repo root. Mirrors the convention used by the sibling
# `test_dockerignore_at_repo_root` module.
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[5]
_DOCKERFILE_PATH: Final[Path] = _REPO_ROOT / "infra" / "docker" / "backend.Dockerfile"


def _read_dockerfile_text() -> str:
    """Return the Dockerfile contents, failing the calling test if missing.

    Raises ``AssertionError`` (rather than calling ``pytest.fail``) so this
    module does not need to import ``pytest``. The ``RobertCraigie/pyright-python``
    pre-push hook runs pyright inside an isolated pre-commit venv that does
    not have ``pytest`` available, so importing it here would trip
    ``reportMissingImports`` at hook time. Pytest still surfaces the failure
    via its ``AssertionError`` reporter, so the test UX is unchanged — this
    pattern mirrors ``test_precommit_pyright_hook_present.py``.
    """
    if not _DOCKERFILE_PATH.is_file():
        msg = (
            f"expected Dockerfile at {_DOCKERFILE_PATH} — issue #363 guardrail "
            "cannot run if the backend Dockerfile has been moved or deleted."
        )
        raise AssertionError(msg)
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
        no_healthcheck_msg = (
            f"no HEALTHCHECK instruction found in {_DOCKERFILE_PATH}. Issue #363 "
            "expects the backend Dockerfile to declare a container-level "
            "liveness probe, even if it is later superseded by a compose-level "
            "healthcheck; dropping it entirely should be a deliberate decision "
            "with its own ADR."
        )
        raise AssertionError(no_healthcheck_msg)
    instruction = match.group(0)
    cmd_idx = instruction.find("CMD")
    if cmd_idx < 0:
        no_cmd_msg = (
            f"HEALTHCHECK instruction in {_DOCKERFILE_PATH} has no `CMD` clause: "
            f"{instruction!r}. The `NONE` form would disable healthchecks "
            "entirely — if that is what you want, update this guardrail and "
            "docs/decisions.md."
        )
        raise AssertionError(no_cmd_msg)
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


def _parse_healthcheck_argv(cmd_line: str) -> list[str]:
    """Parse the HEALTHCHECK `CMD [...]` JSON array into a Python list.

    Failures are surfaced via ``AssertionError`` (rather than ``pytest.fail``)
    for the same reason as ``_read_dockerfile_text``: this module deliberately
    avoids importing ``pytest`` so the ``pyright-python`` pre-push hook's
    isolated venv does not trip on ``reportMissingImports``.
    """
    argv_text = cmd_line.removeprefix("CMD").strip()
    if not argv_text.startswith("["):
        shell_form_msg = (
            f"HEALTHCHECK CMD in {_DOCKERFILE_PATH} is shell form: {cmd_line!r}. "
            "Switch to exec form (JSON array): "
            '`CMD ["curl", "-fsS", "--output", "/dev/null", '
            '"http://localhost:8000/health"]`. Issue #363.'
        )
        raise AssertionError(shell_form_msg)
    try:
        parsed = json.loads(argv_text)
    except json.JSONDecodeError as exc:
        bad_json_msg = (
            f"HEALTHCHECK CMD in {_DOCKERFILE_PATH} is not valid JSON: "
            f"{cmd_line!r} ({exc}). Exec form must be a JSON array of strings."
        )
        raise AssertionError(bad_json_msg) from exc
    if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
        bad_shape_msg = (
            f"HEALTHCHECK CMD in {_DOCKERFILE_PATH} is not a JSON array of "
            f"strings: {cmd_line!r}. Docker's exec-form schema requires "
            '`["arg0", "arg1", ...]`.'
        )
        raise AssertionError(bad_shape_msg)
    return parsed


def test_healthcheck_uses_exec_form() -> None:
    """HEALTHCHECK CMD must be in exec form (JSON array), not shell form.

    Shell form (`CMD python -c ...`) wraps the command in `/bin/sh -c`,
    which adds a shell fork and — in the original implementation —
    resolved `python` via `$PATH` to the venv interpreter. Exec form
    (`CMD ["curl", "-fsS", ...]`) invokes the binary directly with no
    shell. The Docker reference calls exec form the "preferred method."

    Merely checking that the argv starts with `[` is not enough: that
    would also pass for `CMD ["sh", "-c", "curl ..."]`, which smuggles
    the `/bin/sh -c` wrapper back into exec form and defeats the point
    of issue #363. We parse the JSON array, assert the first element is
    the `curl` probe binary, and reject any use of `sh` / `bash` / `-c`
    anywhere in argv to guard against that smuggled-shell form.
    """
    cmd_line = _extract_healthcheck_cmd_line(_read_dockerfile_text())
    argv = _parse_healthcheck_argv(cmd_line)
    assert argv, (
        f"HEALTHCHECK CMD in {_DOCKERFILE_PATH} has an empty argv: "
        f"{cmd_line!r}. Docker requires at least one argument."
    )
    assert argv[0] == "curl", (
        f"HEALTHCHECK CMD in {_DOCKERFILE_PATH} does not invoke `curl` "
        f"directly: {argv!r}. Issue #363 explicitly chose `curl` over a "
        "Python-based probe; if a replacement probe is intended, update "
        "this guardrail and docs/decisions.md with the rationale."
    )
    shell_smuggling = {"sh", "bash", "/bin/sh", "/bin/bash", "-c"}
    offending = shell_smuggling.intersection(argv)
    assert not offending, (
        f"HEALTHCHECK CMD in {_DOCKERFILE_PATH} smuggles a shell into "
        f"exec form: {argv!r}. Removing the `/bin/sh -c` wrapper was the "
        'load-bearing win of issue #363 — `CMD ["sh", "-c", "curl ..."]` '
        "is functionally equivalent to shell form and defeats the fix."
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


def test_healthcheck_curl_is_silent_on_success() -> None:
    """HEALTHCHECK `curl` must discard the `/health` response body.

    `curl -fsS` is quieter than default curl (no progress bar, no error
    noise), but it still writes the HTTP response body to stdout on
    success. For a `/health` endpoint that returns `{"status":"ok"}`,
    that means Docker's healthcheck log captures `{"status":"ok"}` every
    30s forever, inflating log volume with useless noise. Routing stdout
    to `/dev/null` (either via `--output /dev/null` or `-o /dev/null`)
    keeps the probe truly silent on success while preserving the `-S`
    error-on-failure surface.
    """
    cmd_line = _extract_healthcheck_cmd_line(_read_dockerfile_text())
    argv = _parse_healthcheck_argv(cmd_line)
    silent_flags = {"--output", "-o"}
    assert silent_flags.intersection(argv), (
        f"HEALTHCHECK CMD in {_DOCKERFILE_PATH} does not silence curl "
        f"stdout: {argv!r}. Add `--output /dev/null` (or `-o /dev/null`) "
        "so the probe does not spam the healthcheck log with the "
        "response body every 30s. Issue #363 / review round 2."
    )
