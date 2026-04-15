"""Standalone `SkillManifest` validator for the skill-authoring dev loop.

Runs `SkillLoader.load` against a `skills_dir` without booting FastAPI or
importing any of the heavy extraction-stack dependencies (Docling,
LangExtract, PyMuPDF, Ollama HTTP client). On success, prints
`✔ N skills validated` to stdout and exits 0. On failure, prints the
aggregated reason to stderr and exits non-zero.

Invocation
----------
- Via Taskfile:   `task skills:validate [SKILLS_DIR=./path]`
- Via module:     `uv run python -m scripts.validate_skills [<skills_dir>]`
- Programmatic:   `from scripts.validate_skills import validate; validate(path)`

The path argument takes precedence over `Settings().skills_dir`; when both
are absent, the default from `Settings` is used.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

from app.core.config import Settings
from app.exceptions import SkillValidationFailedError
from app.features.extraction.skills.skill_loader import SkillLoader


def validate(skills_dir: Path | str) -> int:
    """Validate `skills_dir` and return a process exit code.

    Accepts either a `Path` or a string path and normalizes to `Path`
    before delegating to `SkillLoader`. Returns 0 on full success
    (including the empty-directory case, which `SkillLoader` treats as a
    warn-and-continue). Returns 1 on any aggregated validation failure,
    with the reason written to stderr.

    `SkillLoader` emits a `skill_manifest_empty` structlog warning on
    empty directories; structlog's default `PrintLoggerFactory` writes
    to stdout, which would contaminate the promised single result line.
    The `redirect_stdout(sys.stderr)` wrapper keeps stdout clean for the
    function's own output regardless of how the caller has (or hasn't)
    configured structlog, without mutating any global logging state.
    """
    skills_dir = Path(skills_dir)
    loader = SkillLoader()
    try:
        with contextlib.redirect_stdout(sys.stderr):
            loaded = loader.load(skills_dir)
    except SkillValidationFailedError as exc:
        sys.stderr.write(_format_error(exc) + "\n")
        return 1

    sys.stdout.write(f"\u2714 {len(loaded)} skills validated\n")
    return 0


def _format_error(exc: SkillValidationFailedError) -> str:
    """Render a `SkillValidationFailedError` as a single human-readable block.

    `SkillLoader` already includes file or directory context inside the
    aggregated `reason`, so prefer that message verbatim to avoid
    duplicating paths in CLI stderr output.
    """
    if exc.params is None:
        return str(exc)
    dumped = exc.params.model_dump()
    file_path = dumped.get("file", "") or ""
    reason = dumped.get("reason", "") or ""
    if reason:
        return str(reason)
    if file_path:
        return str(file_path)
    return str(exc)


def _default_skills_dir() -> Path:
    return Settings().skills_dir


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Accepts at most one positional argument: the skills directory.

    Extra positional arguments are rejected with a non-zero exit code so a
    typo like `validate_skills ./skills /does/not/exist` surfaces loudly
    instead of silently validating the first path and dropping the rest.
    """
    args = sys.argv[1:] if argv is None else argv
    if len(args) > 1:
        sys.stderr.write(
            "usage: python -m scripts.validate_skills [<skills_dir>]\n"
            f"error: expected at most 1 positional argument, got {len(args)}\n",
        )
        return 2
    skills_dir = Path(args[0]) if args else _default_skills_dir()
    return validate(skills_dir)


if __name__ == "__main__":
    raise SystemExit(main())
