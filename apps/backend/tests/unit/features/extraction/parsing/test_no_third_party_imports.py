"""Static AST scan: the parsing abstraction layer must be free of third-party imports.

This test guarantees the "zero third-party imports" technical constraint mechanically.
It walks the AST of each abstraction file and rejects any import whose root module is
docling, pymupdf, fitz, langextract, or pydantic.
"""

import ast

import pytest

from tests._paths import EXTRACTION_ROOT as _EXTRACTION_ROOT

_FORBIDDEN_ROOTS = frozenset({"docling", "pymupdf", "fitz", "langextract", "pydantic"})

_PARSING_DIR = _EXTRACTION_ROOT / "parsing"

_ABSTRACTION_FILES = (
    "document_parser.py",
    "parsed_document.py",
    "text_block.py",
    "bounding_box.py",
    "docling_config.py",
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


@pytest.mark.parametrize("filename", _ABSTRACTION_FILES)
def test_abstraction_file_has_no_forbidden_imports(filename: str) -> None:
    path = _PARSING_DIR / filename
    assert path.exists(), f"expected abstraction file at {path}"

    roots = _collect_root_imports(path.read_text())

    forbidden = roots & _FORBIDDEN_ROOTS
    assert not forbidden, f"{filename} imports forbidden modules: {sorted(forbidden)}"
