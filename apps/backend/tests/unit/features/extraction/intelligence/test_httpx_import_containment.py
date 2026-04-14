"""Static AST scan: httpx imports must be contained to ollama_gemma_provider.py.

The dual-interface provider is the ONE file in the extraction feature allowed
to know about HTTP clients. PDFX-E007-F004 will later add an import-linter
contract that enforces this mechanically; until then, this AST scan is the
enforcement mechanism. Any new file under `app/features/extraction/` that
imports `httpx` fails this test.
"""

from __future__ import annotations

import ast
from pathlib import Path

_EXTRACTION_ROOT = Path(__file__).resolve().parents[5] / "app" / "features" / "extraction"
_ALLOWED_FILES = frozenset(
    {
        "ollama_gemma_provider.py",
    },
)


def _collect_root_imports(source: str) -> set[str]:
    tree = ast.parse(source)
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_httpx_import_contained_to_ollama_gemma_provider() -> None:
    offenders: list[str] = []
    for py_file in _EXTRACTION_ROOT.rglob("*.py"):
        if py_file.name in _ALLOWED_FILES:
            continue
        roots = _collect_root_imports(py_file.read_text())
        if "httpx" in roots:
            offenders.append(str(py_file.relative_to(_EXTRACTION_ROOT)))
    assert not offenders, f"httpx imported outside the allowed containment file(s): {offenders}"
