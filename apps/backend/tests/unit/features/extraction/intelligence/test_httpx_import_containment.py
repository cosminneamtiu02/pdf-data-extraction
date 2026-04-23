"""Static AST scan: httpx imports must be contained to allowed files.

Two files in the extraction feature are authorized to import httpx:
  1. ``ollama_gemma_provider.py`` — the dual-interface Ollama provider.
  2. ``ollama_health_probe.py`` — the readiness probe (PDFX-E007-F001).

PDFX-E007-F004 adds an import-linter contract (C6) that enforces this
mechanically; this AST scan is the complementary enforcement mechanism.
"""

from __future__ import annotations

import ast

from tests._paths import EXTRACTION_ROOT as _EXTRACTION_ROOT

_ALLOWED_FILES = frozenset(
    {
        "ollama_gemma_provider.py",
        "ollama_health_probe.py",
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
