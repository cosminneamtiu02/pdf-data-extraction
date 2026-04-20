"""Guard rails around the runtime-stage `apt-get install` in backend.Dockerfile.

Issue #362 flagged the runtime-stage `apt-get install` as non-reproducible
because it does not pin package versions (e.g. `tesseract-ocr=5.3.0-2`).

The resolution (option **b** in the issue body) is NOT to pin: the base image
is already pinned by SHA256 digest (`python:3.13-slim@sha256:d168b8d9…`),
which is the real reproducibility boundary. Pinning Debian packages on top of
a digest-pinned base would block Debian security updates without adding
reproducibility that the digest doesn't already provide — the same digest
always resolves to the same layers, so the same `apt-get install` always sees
the same Debian suite snapshot at build time.

The intent is therefore:

1. FROM lines must remain digest-pinned (`@sha256:…`). Losing the digest is
   what turns floating apt packages into a reproducibility hole, and is the
   failure mode this test guards against.
2. The runtime-stage `apt-get install` block must carry an above-the-line
   guard comment containing the stable marker
   `reproducibility-boundary:digest-only`, so future contributors who skim
   the Dockerfile see the rationale before reintroducing brittle pins.
3. No `apt-get install` line may pin a package with `=<version>`. Any
   contributor who wants to pin must first remove the digest pin and justify
   the tradeoff, which is a deliberate, reviewed change — not a silent drift.

If this test fails, do NOT add `=<version>` pins to make it pass. Read the
guard comment in `infra/docker/backend.Dockerfile` and the rationale in the
PR that closed issue #362.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

# parents[5] walks: this file -> meta/ -> unit/ -> tests/ -> backend/ ->
# apps/ -> repo root. Mirrors the convention used by
# `tests/unit/docker/test_dockerignore_at_repo_root.py`.
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[5]
_DOCKERFILE_PATH: Final[Path] = _REPO_ROOT / "infra" / "docker" / "backend.Dockerfile"

_MARKER: Final[str] = "reproducibility-boundary:digest-only"
# How many lines above the `apt-get install` line the marker must appear.
# Tight enough to prove the marker is topically adjacent to the install block,
# loose enough to accommodate a multi-line comment block.
_MARKER_PROXIMITY_LINES: Final[int] = 40

_APT_INSTALL_RE: Final[re.Pattern[str]] = re.compile(r"\bapt-get\s+install\b")
# Matches a pin like `tesseract-ocr=5.3.0-2` but NOT a bare
# `--key=value` flag (which starts with `-`) and NOT shell variables
# (which start with `$`). We look for a Debian-style package name token
# (starts with an alphanumeric, contains letters/digits/dots/plus/dash)
# followed immediately by `=` and a version-ish token.
_APT_PIN_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w$-])[a-z0-9][a-z0-9.+\-]*=[a-zA-Z0-9][\w.:+~\-]*"
)
_FROM_DIGEST_RE: Final[re.Pattern[str]] = re.compile(r"^\s*FROM\s+\S*@sha256:[0-9a-f]{64}")
_ARG_DIGEST_RE: Final[re.Pattern[str]] = re.compile(r"^\s*ARG\s+\w+\s*=\s*\S*@sha256:[0-9a-f]{64}")


def _dockerfile_lines() -> list[str]:
    if not _DOCKERFILE_PATH.is_file():
        msg = f"expected backend Dockerfile at {_DOCKERFILE_PATH}"
        raise AssertionError(msg)
    return _DOCKERFILE_PATH.read_text(encoding="utf-8").splitlines()


def _apt_install_line_indices(lines: list[str]) -> list[int]:
    return [i for i, line in enumerate(lines) if _APT_INSTALL_RE.search(line)]


def _from_line_indices(lines: list[str]) -> list[int]:
    return [i for i, line in enumerate(lines) if line.lstrip().startswith("FROM ")]


def _resolve_from_reference(lines: list[str], from_line: str) -> str:
    """Return the concrete image reference for a FROM line.

    If the FROM uses an ARG (e.g. `FROM ${PYTHON_IMAGE}`), look up the
    most-recent `ARG <NAME>=<value>` default above the FROM and substitute.
    This mirrors Docker's own ARG-before-FROM scoping rules.
    """
    tokens = from_line.split()
    # Expect: ["FROM", "<image-ref>", ...maybe "AS", "<stage>"]
    if len(tokens) < 2:
        msg = f"malformed FROM line: {from_line!r}"
        raise AssertionError(msg)
    image_ref = tokens[1]
    arg_ref = re.fullmatch(r"\$\{(\w+)\}|\$(\w+)", image_ref)
    if arg_ref is None:
        return image_ref
    arg_name = arg_ref.group(1) or arg_ref.group(2)
    arg_default_re = re.compile(rf"^\s*ARG\s+{re.escape(arg_name)}\s*=\s*(\S+)")
    for line in lines:
        match = arg_default_re.match(line)
        if match:
            return match.group(1)
    msg = (
        f"FROM references ARG {arg_name!r} but no `ARG {arg_name}=<default>` "
        f"was found in {_DOCKERFILE_PATH}"
    )
    raise AssertionError(msg)


def test_runtime_apt_install_has_reproducibility_marker() -> None:
    """A guard comment with the stable marker must precede `apt-get install`.

    The marker is the anchor for this test and for any future contributor
    skimming the Dockerfile. Moving the install block without moving the
    marker fails this test, which is the intended behavior.
    """
    lines = _dockerfile_lines()
    install_indices = _apt_install_line_indices(lines)
    assert install_indices, (
        f"{_DOCKERFILE_PATH} contains no `apt-get install` line. If the runtime "
        f"stage no longer installs apt packages, delete this test too."
    )
    for install_idx in install_indices:
        window_start = max(0, install_idx - _MARKER_PROXIMITY_LINES)
        window = lines[window_start:install_idx]
        assert any(_MARKER in line for line in window), (
            f"{_DOCKERFILE_PATH} line {install_idx + 1} runs `apt-get install` "
            f"but no `{_MARKER}` marker comment appears within the preceding "
            f"{_MARKER_PROXIMITY_LINES} lines. Issue #362 requires this marker "
            f"so the intentional non-pinning is visible to future contributors."
        )


def test_from_lines_are_digest_pinned() -> None:
    """Every FROM must resolve to an image reference carrying `@sha256:…`.

    The digest is the reproducibility boundary. If a FROM loses its digest
    (or references an ARG whose default lost its digest), the justification
    for leaving apt packages floating collapses and this test fires.
    """
    lines = _dockerfile_lines()
    from_indices = _from_line_indices(lines)
    assert from_indices, f"{_DOCKERFILE_PATH} contains no FROM line"
    for idx in from_indices:
        resolved = _resolve_from_reference(lines, lines[idx])
        assert "@sha256:" in resolved, (
            f"{_DOCKERFILE_PATH} line {idx + 1} FROM resolves to {resolved!r}, "
            f"which is not digest-pinned. Issue #362 requires digest-pinned "
            f"bases because they ARE the reproducibility boundary that lets "
            f"apt packages stay floating for security updates."
        )


def test_no_apt_install_line_pins_package_version() -> None:
    """No `apt-get install` line may carry `pkg=version` pins.

    Enforces option (b) from issue #362: apt packages intentionally float so
    they can receive Debian security updates within the digest-pinned base
    image. Pinning defeats the point; if a future PR really needs to pin a
    specific package, it must first remove the digest from the base image
    and justify the tradeoff in its own review.
    """
    lines = _dockerfile_lines()
    install_indices = _apt_install_line_indices(lines)
    # `apt-get install` can continue over backslash-terminated lines. Walk
    # downward from each install anchor until we hit a line that does NOT end
    # with a backslash — that's the end of the logical RUN command.
    offending: list[tuple[int, str, str]] = []
    for start in install_indices:
        idx = start
        while idx < len(lines):
            line = lines[idx]
            # Strip trailing comments before scanning — a `# pkg=ver` inside a
            # comment should not be flagged.
            code, _, _comment = line.partition("#")
            offending.extend(
                (idx + 1, match.group(0), line.rstrip()) for match in _APT_PIN_RE.finditer(code)
            )
            if not code.rstrip().endswith("\\"):
                break
            idx += 1
    assert not offending, (
        f"{_DOCKERFILE_PATH} has version-pinned apt packages, which contradicts "
        f"the issue #362 resolution (option b — intentional floating under a "
        f"digest-pinned base). Offending pins: {offending!r}. If you really do "
        f"need to pin, remove the `@sha256:` digest from the base image first "
        f"and justify the tradeoff in your PR."
    )
