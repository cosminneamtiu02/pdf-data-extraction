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

import sys
from pathlib import Path

import structlog

from app.core.config import Settings
from app.exceptions import SkillValidationFailedError
from app.features.extraction.skills.skill_loader import SkillLoader


def validate(skills_dir: Path) -> int:
    """Validate `skills_dir` and return a process exit code.

    Returns 0 on full success (including the empty-directory case, which
    `SkillLoader` treats as a warn-and-continue). Returns 1 on any
    aggregated validation failure, with the reason written to stderr.
    """
    loader = SkillLoader()
    try:
        loaded = loader.load(skills_dir)
    except SkillValidationFailedError as exc:
        sys.stderr.write(_format_error(exc) + "\n")
        return 1

    sys.stdout.write(f"\u2714 {len(loaded)} skills validated\n")
    return 0


def _format_error(exc: SkillValidationFailedError) -> str:
    """Render a `SkillValidationFailedError` as a single human-readable block."""
    if exc.params is None:
        return str(exc)
    dumped = exc.params.model_dump()
    file_path = dumped.get("file", "") or ""
    reason = dumped.get("reason", "") or ""
    if file_path and reason:
        return f"{file_path}: {reason}"
    return str(reason) if reason else str(exc)


def _default_skills_dir() -> Path:
    return Settings().skills_dir


def _configure_cli_logging() -> None:
    """Route structlog output to stderr so stdout stays clean for the result line.

    `SkillLoader` emits a `skill_manifest_empty` warning when called against an
    empty directory (the default `apps/backend/skills/` case). Without this
    configuration, structlog's default `PrintLoggerFactory` writes to stdout,
    which would contaminate the single `✔ N skills validated` line that the
    CLI contract promises. CLI tools put data on stdout and diagnostics on
    stderr; we enforce that here for the entry-point path only — programmatic
    `validate()` callers keep whatever structlog config they set up.
    """
    structlog.configure(
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Accepts one positional argument: the skills directory."""
    _configure_cli_logging()
    args = sys.argv[1:] if argv is None else argv
    skills_dir = Path(args[0]) if args else _default_skills_dir()
    return validate(skills_dir)


if __name__ == "__main__":
    raise SystemExit(main())
