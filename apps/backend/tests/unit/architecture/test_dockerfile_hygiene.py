"""Hygiene checks on `infra/docker/backend.Dockerfile`.

Static assertions about the backend runtime image's Dockerfile — kept in
the backend test tree so they run inside the canonical `task check` gate.
Catches drift of hardening invariants the repo has agreed to across PRs
so a future copy-paste does not silently regress the posture.

Issue #213: without an `init`-style process at PID 1 (tini / dumb-init),
signals sent to the container (`docker stop` → SIGTERM) are handled only
by uvicorn itself. Sub-processes that Docling / PyMuPDF / OCR tooling
spawn during extraction may not be reaped or forwarded signals, leaving
zombies behind and slowing graceful shutdown. The fix is to install
`tini` in the runtime stage and set it as the `ENTRYPOINT` so it becomes
PID 1 and forwards signals to uvicorn while reaping zombies.

Issue #139 / #192 added the apt-get layer hygiene convention: every
`apt-get install` in the runtime stage must pair with
`--no-install-recommends` and finish with
`rm -rf /var/lib/apt/lists/*` in the *same* RUN so the apt cache does
not bloat the image layer.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from ._linter_subprocess import REPO_ROOT

_BACKEND_DOCKERFILE: Final[Path] = REPO_ROOT / "infra" / "docker" / "backend.Dockerfile"


def _read_dockerfile() -> str:
    return _BACKEND_DOCKERFILE.read_text()


def _runtime_stage_text(dockerfile: str) -> str:
    """Return the substring of `dockerfile` starting at the runtime `FROM` line.

    The runtime stage is the *second* `FROM` directive. The builder stage
    also installs apt packages but is discarded at the end of the multi-stage
    build, so its hygiene does not affect the shipped image. Scoping these
    assertions to the runtime stage keeps them focused on the final layers.
    """
    from_lines = [m.start() for m in re.finditer(r"(?m)^FROM ", dockerfile)]
    assert len(from_lines) >= 2, (
        f"expected at least two FROM directives (builder + runtime); "
        f"found {len(from_lines)} in backend.Dockerfile"
    )
    return dockerfile[from_lines[1] :]


def _collapse_backslash_continuations(text: str) -> str:
    """Join lines that end in a backslash continuation into a single logical line.

    Dockerfile RUN directives commonly span multiple physical lines via
    trailing backslashes — ``RUN apt-get update \\`` then indented
    ``&& apt-get install ...`` on the next line. For regex-based hygiene
    checks we want to see each RUN as a single logical line, so collapse
    the escape + newline + leading whitespace into a single space.
    """
    return re.sub(r"\\\n\s*", " ", text)


def test_runtime_stage_installs_tini_with_no_install_recommends() -> None:
    """Runtime stage must install `tini` via apt with `--no-install-recommends`.

    tini is the PID 1 init wrapper: it reaps orphaned zombie processes and
    forwards signals to its single child (uvicorn). Without it, a `docker
    stop` leaves Docling / PyMuPDF / OCR sub-processes zombied and
    potentially unkilled. `--no-install-recommends` keeps the package cost
    to just tini itself (see #192 image-size budget).
    """
    runtime = _collapse_backslash_continuations(_runtime_stage_text(_read_dockerfile()))
    assert re.search(
        r"apt-get install[^\n]*--no-install-recommends[^\n]*\btini\b",
        runtime,
    ), (
        "runtime stage in infra/docker/backend.Dockerfile does not install tini via "
        "`apt-get install --no-install-recommends ... tini ...`. Issue #213 requires "
        "tini as PID 1 for signal forwarding and zombie reaping."
    )


def test_runtime_stage_entrypoint_is_tini_exec_wrapper() -> None:
    """Runtime stage must set `ENTRYPOINT ["/usr/bin/tini", "--"]`.

    Exec-form JSON array is mandatory so Docker does not wrap in /bin/sh,
    which would insert a shell as PID 1 and defeat the point of tini. The
    `--` sentinel tells tini to treat the remaining CMD argv literally,
    even if a CMD element starts with a dash.
    """
    dockerfile = _read_dockerfile()
    runtime = _runtime_stage_text(dockerfile)
    assert re.search(
        r'(?m)^ENTRYPOINT\s*\[\s*"/usr/bin/tini"\s*,\s*"--"\s*\]',
        runtime,
    ), (
        'runtime stage missing `ENTRYPOINT ["/usr/bin/tini", "--"]`. Issue #213 '
        "requires tini as exec-form ENTRYPOINT so it becomes PID 1 and forwards "
        "SIGTERM / SIGINT to uvicorn while reaping zombies."
    )


def test_runtime_stage_cmd_preserved_for_uvicorn() -> None:
    """Runtime stage's CMD must still start uvicorn on 0.0.0.0:8000.

    tini is added as an ENTRYPOINT wrapper; the existing CMD (the uvicorn
    argv) must be preserved so compose / k8s overrides still work and the
    healthcheck on :8000 still answers.
    """
    runtime = _runtime_stage_text(_read_dockerfile())
    assert re.search(
        r'(?m)^CMD\s*\[\s*"uvicorn"\s*,\s*"app\.main:app"[^\]]*"--host"\s*,\s*"0\.0\.0\.0"'
        r'[^\]]*"--port"\s*,\s*"8000"',
        runtime,
    ), (
        'runtime CMD must remain `["uvicorn", "app.main:app", "--host", '
        '"0.0.0.0", "--port", "8000"]` (exec form). Issue #213 adds tini only as '
        "ENTRYPOINT — CMD must not change."
    )


def test_every_apt_install_run_has_layer_hygiene() -> None:
    """Every `apt-get install` RUN in the runtime stage must pair with
    `--no-install-recommends` and `rm -rf /var/lib/apt/lists/*` in the *same*
    RUN directive.

    Splitting the cache cleanup into a separate RUN would leave the apt
    lists in a prior layer and bloat the image. This is the #139 / #192
    image-size hygiene convention. Guarding it here prevents future
    `apt-get install foo` additions from silently breaking it.
    """
    runtime = _runtime_stage_text(_read_dockerfile())

    # Collect each `RUN ...` block, honoring backslash line continuations.
    run_blocks: list[str] = []
    lines = runtime.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("RUN "):
            block_lines: list[str] = [lines[i]]
            while block_lines[-1].rstrip().endswith("\\") and i + 1 < len(lines):
                i += 1
                block_lines.append(lines[i])
            run_blocks.append("\n".join(block_lines))
        i += 1

    offenders: list[str] = []
    for block in run_blocks:
        if "apt-get install" not in block:
            continue
        if "--no-install-recommends" not in block:
            offenders.append(
                "missing --no-install-recommends in:\n" + block,
            )
        if "rm -rf /var/lib/apt/lists/*" not in block:
            offenders.append(
                "missing `rm -rf /var/lib/apt/lists/*` in same RUN:\n" + block,
            )

    assert not offenders, (
        "runtime stage apt-get hygiene violations (issue #139 / #192):\n"
        + "\n---\n".join(offenders)
    )
