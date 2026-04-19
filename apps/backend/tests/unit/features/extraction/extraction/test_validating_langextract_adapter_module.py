"""Sacred Rule #1 regression for issue #228.

`_ValidatingLangExtractAdapter` used to be declared inline inside
`extraction_engine.py`, giving that file two top-level classes (the
adapter and `ExtractionEngine` itself). CLAUDE.md Sacred Rule #1
mandates one class per file. This test pins down the split:

- The adapter lives in its own intra-subpackage-private module
  `_validating_langextract_adapter.py`.
- `extraction_engine.py` still declares exactly one class
  (`ExtractionEngine`) at the module top level.
- `extraction_engine.py` re-imports the adapter from the new module so
  existing callers (tests and internal uses) keep working unchanged.

The split preserves the C5 LangExtract containment contract: both files
sit under `app.features.extraction.extraction`, which is the package
intended for LangExtract orchestration.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.features.extraction.extraction._validating_langextract_adapter import (
    _ValidatingLangExtractAdapter as AdapterFromOwnModule,
)
from app.features.extraction.extraction.extraction_engine import (
    _ValidatingLangExtractAdapter as AdapterReExported,
)

_ENGINE_PATH = (
    Path(__file__).resolve().parents[5]
    / "app"
    / "features"
    / "extraction"
    / "extraction"
    / "extraction_engine.py"
)
_ADAPTER_PATH = (
    Path(__file__).resolve().parents[5]
    / "app"
    / "features"
    / "extraction"
    / "extraction"
    / "_validating_langextract_adapter.py"
)


def _top_level_class_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    return [node.name for node in tree.body if isinstance(node, ast.ClassDef)]


def test_extraction_engine_module_declares_exactly_one_class() -> None:
    """Sacred Rule #1: `extraction_engine.py` contains exactly `ExtractionEngine`."""
    assert _top_level_class_names(_ENGINE_PATH) == ["ExtractionEngine"]


def test_validating_adapter_module_declares_exactly_one_class() -> None:
    """Sacred Rule #1: the adapter module contains exactly the adapter class."""
    assert _top_level_class_names(_ADAPTER_PATH) == ["_ValidatingLangExtractAdapter"]


def test_extraction_engine_reexports_the_same_adapter_class() -> None:
    """The re-export preserves identity so `isinstance` checks and monkeypatches
    continue working on both import paths.
    """
    assert AdapterReExported is AdapterFromOwnModule
