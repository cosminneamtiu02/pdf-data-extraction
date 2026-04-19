"""Containment scan: `langextract` is only allowed in allowlisted files.

This is the mechanical guarantee behind AC4 of PDFX-E004-F003: LangExtract
imports are allowed only inside the extraction engine (`extraction/
extraction_engine.py`), the adjacent validating adapter
(`extraction/_validating_langextract_adapter.py`, split out in issue #228
to satisfy Sacred Rule #1), and the intelligence-layer plugin registration
file (`intelligence/ollama_gemma_provider.py`, PDFX-E004-F002). The test
walks the entire `app/features/extraction/` subtree and rejects any
`import langextract` / `from langextract...` outside of those files.

PDFX-E007-F004 landed the C5 import-linter contract covering the same
allowlist (see `apps/backend/architecture/import-linter-contracts.ini`).
This AST-scan test is now a redundant defense-in-depth check alongside
the import-linter gate.
"""

from __future__ import annotations

import ast
from pathlib import Path

_EXTRACTION_ROOT = Path(__file__).resolve().parents[5] / "app" / "features" / "extraction"

# LangExtract imports are permitted only in the engine file itself, the
# adjacent validating adapter module (issue #228 split), and in the
# intelligence subpackage's plugin registration file (PDFX-E004-F002).
# Every other file under `features/extraction/` must be langextract-free.
_ALLOWED_FILES = frozenset(
    {
        Path("extraction") / "extraction_engine.py",
        Path("extraction") / "_validating_langextract_adapter.py",
        Path("intelligence") / "ollama_gemma_provider.py",
    },
)

_FORBIDDEN_ROOT = "langextract"


def _imports_langextract(source: str) -> bool:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] == _FORBIDDEN_ROOT for alias in node.names):
                return True
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.level == 0
            and node.module.split(".")[0] == _FORBIDDEN_ROOT
        ):
            return True
    return False


def test_langextract_imports_are_contained_to_allowlisted_files() -> None:
    offenders: list[str] = []
    for py_file in _EXTRACTION_ROOT.rglob("*.py"):
        rel = py_file.relative_to(_EXTRACTION_ROOT)
        if rel in _ALLOWED_FILES:
            continue
        if _imports_langextract(py_file.read_text()):
            offenders.append(str(rel))

    allowed = sorted(str(p) for p in _ALLOWED_FILES)
    assert not offenders, (
        f"files outside the LangExtract allowlist {allowed} import langextract: {offenders}"
    )
