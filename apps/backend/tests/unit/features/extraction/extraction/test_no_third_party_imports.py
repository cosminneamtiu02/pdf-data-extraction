"""Containment scan: `langextract` is only allowed inside `extraction/`.

This is the mechanical guarantee behind AC4 of PDFX-E004-F003: "LangExtract
imports appear only in `extraction_engine.py`." The test walks the entire
`app/features/extraction/` subtree and rejects any `import langextract` (or
`from langextract...`) outside of files in `features/extraction/extraction/`
or the intelligence-layer plugin registration file(s) permitted by the spec.

Once PDFX-E007-F004 adds import-linter contracts, this test becomes a
defensive double-check.
"""

from __future__ import annotations

import ast
from pathlib import Path

_EXTRACTION_ROOT = Path(__file__).resolve().parents[5] / "app" / "features" / "extraction"

# LangExtract imports are permitted only in the engine file itself, and in
# the intelligence subpackage's plugin registration file (PDFX-E004-F002).
# Every other file under `features/extraction/` must be langextract-free.
_ALLOWED_FILES = frozenset(
    {
        Path("extraction") / "extraction_engine.py",
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


def test_langextract_imports_are_contained_to_extraction_engine() -> None:
    offenders: list[str] = []
    for py_file in _EXTRACTION_ROOT.rglob("*.py"):
        rel = py_file.relative_to(_EXTRACTION_ROOT)
        if rel in _ALLOWED_FILES:
            continue
        if _imports_langextract(py_file.read_text()):
            offenders.append(str(rel))

    assert not offenders, f"files outside of extraction_engine.py import langextract: {offenders}"
