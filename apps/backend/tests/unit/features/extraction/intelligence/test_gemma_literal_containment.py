"""Static AST scan: no Gemma model tag literals in extraction source.

The model tag is configuration, not code. A hardcoded tag means changing the
model version requires a code edit instead of an env var flip. This test
enforces the invariant by grepping AST string constants for a model-tag shape
(`gemma<digit>`), which matches actual Ollama tags like `gemma2:2b`, `gemma4:1b`
without false-flagging conceptual mentions of "Gemma 4" in docstrings.

Allowlist: regex pattern strings passed to the LangExtract `register(...)`
decorator (e.g. `r"^gemma"`) would not match this pattern anyway (no digit),
so they need no special-casing. The allowlist of *files* is empty — this
invariant holds everywhere inside `features/extraction/`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_EXTRACTION_ROOT = Path(__file__).resolve().parents[5] / "app" / "features" / "extraction"

_MODEL_TAG_PATTERN = re.compile(r"gemma\d", re.IGNORECASE)


def _scan_file_for_tags(py_file: Path) -> list[tuple[str, int, str]]:
    tree = ast.parse(py_file.read_text())
    return [
        (
            str(py_file.relative_to(_EXTRACTION_ROOT)),
            node.lineno,
            node.value,
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _MODEL_TAG_PATTERN.search(node.value)
    ]


def test_no_hardcoded_gemma_model_tag_in_extraction_source() -> None:
    offenders: list[tuple[str, int, str]] = []
    for py_file in _EXTRACTION_ROOT.rglob("*.py"):
        offenders.extend(_scan_file_for_tags(py_file))
    assert not offenders, f"Gemma model tag literals found in extraction source: {offenders}"
