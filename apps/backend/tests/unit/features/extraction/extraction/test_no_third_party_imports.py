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

# LangExtract imports are permitted inside the extraction subpackage itself
# (the engine) and optionally in the intelligence subpackage for plugin
# registration of OllamaGemmaProvider (PDFX-E004-F002).
_ALLOWED_SUBPATHS = (
    Path("extraction"),
    Path("intelligence") / "ollama_gemma_provider.py",
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


def _is_allowed(rel: Path) -> bool:
    return any(
        rel == allowed or (allowed.suffix == "" and allowed in rel.parents)
        for allowed in _ALLOWED_SUBPATHS
    )


def test_langextract_imports_are_contained_to_extraction_subpackage() -> None:
    offenders: list[str] = []
    for py_file in _EXTRACTION_ROOT.rglob("*.py"):
        rel = py_file.relative_to(_EXTRACTION_ROOT)
        if _is_allowed(rel):
            continue
        if _imports_langextract(py_file.read_text()):
            offenders.append(str(rel))

    assert not offenders, (
        f"files outside of the allowed extraction layer import langextract: {offenders}"
    )
