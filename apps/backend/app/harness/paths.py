"""Path derivation helpers for the harness.

Walks up from this file to locate the project root (the directory that
holds ``data/`` and ``iterations/``). No hardcoded absolute paths.
"""

from __future__ import annotations

import re
from pathlib import Path

# apps/backend/app/harness/paths.py -> apps/backend/app/harness
# -> apps/backend/app -> apps/backend -> apps -> <project root>
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent

_PDF_ID_RE = re.compile(r"^\d{3}$")


def project_root() -> Path:
    """Absolute path to the project root (above ``apps/``)."""
    return _PROJECT_ROOT


def data_dir() -> Path:
    root = _PROJECT_ROOT / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root


def iterations_dir() -> Path:
    root = _PROJECT_ROOT / "iterations"
    root.mkdir(parents=True, exist_ok=True)
    return root


def run_dir(run_id: int) -> Path:
    return iterations_dir() / f"run-{run_id}"


def annotated_dir(run_id: int) -> Path:
    return run_dir(run_id) / "annotated"


def expected_dir(run_id: int) -> Path:
    return run_dir(run_id) / "expected"


def results_path(run_id: int) -> Path:
    return run_dir(run_id) / "results.json"


def feedback_path(run_id: int) -> Path:
    return run_dir(run_id) / "feedback.json"


def source_pdf_path(pdf_id: str) -> Path:
    return data_dir() / pdf_id / "source.pdf"


def list_pdf_ids() -> list[str]:
    """Return zero-padded PDF ids (``000``, ``001``, ...) in lexical order.

    A directory is considered a PDF only if it has a ``source.pdf`` file.
    """
    root = data_dir()
    ids: list[str] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if not _PDF_ID_RE.match(child.name):
            continue
        if not (child / "source.pdf").exists():
            continue
        ids.append(child.name)
    return ids


def next_run_id() -> int:
    """Find the next free ``run-N`` id (1-based)."""
    root = iterations_dir()
    used: list[int] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        name = child.name
        if not name.startswith("run-"):
            continue
        try:
            used.append(int(name[len("run-") :]))
        except ValueError:
            continue
    return (max(used) + 1) if used else 1
