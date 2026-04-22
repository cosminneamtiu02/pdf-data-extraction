"""Meta-enforcement tests: inject violations into a scratch tree and prove contracts catch them.

Scenarios U7 (third-party containment), U8 (layer DAG), and U9 (clean-slate baseline)
from PDFX-E007-F004's verifiable spec. Each test copies `apps/backend/app` into a
pytest tmp directory, optionally patches one source file with a forbidden import,
copies the real contracts INI, then runs `lint-imports` as a subprocess against the
scratch tree and asserts the expected contract broke (or, for U9, asserts nothing
broke).

These tests are the dynamic complement of `test_import_linter_contracts.py`,
which only verifies the contract file's structural shape. They prove the
contracts are actually enforced in practice, without touching the real
codebase or depending on any particular existing violation or carve-out.

The shared subprocess plumbing - path constants, `lint-imports` binary lookup,
scratch-tree builders, and the future-import-aware injection helper - lives in
`tests/unit/architecture/_linter_subprocess.py`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, NamedTuple

import pytest

from ._linter_subprocess import (
    copy_app_tree,
    copy_contracts,
    inject_import_line,
    run_lint_imports,
)


class ViolationCase(NamedTuple):
    """A single (file-to-mutate, line-to-inject, expected-contract-keyword) row.

    Used as parametrization carrier for both U7 (third-party containment)
    and U8 (layer DAG) tests so the harness is symmetric across both.
    NamedTuple is preferred over dataclass here to avoid spinning up a
    separate file per "type" (CLAUDE.md one-class-per-file targets domain
    types, not pytest parametrize carriers).
    """

    target_rel_path: str
    inject_line: str
    expected_contract_keyword: str
    label: str


_THIRD_PARTY_CASES: Final[tuple[ViolationCase, ...]] = (
    ViolationCase(
        target_rel_path="features/extraction/intelligence/intelligence_provider.py",
        inject_line="import docling",
        expected_contract_keyword="docling",
        label="docling into intelligence (AC4)",
    ),
    ViolationCase(
        target_rel_path="features/extraction/parsing/docling_config.py",
        inject_line="import langextract",
        expected_contract_keyword="langextract",
        label="langextract into parsing",
    ),
    ViolationCase(
        target_rel_path="features/extraction/coordinates/offset_index.py",
        inject_line="import httpx",
        expected_contract_keyword="httpx",
        label="httpx into coordinates",
    ),
    ViolationCase(
        target_rel_path="features/extraction/intelligence/correction_prompt_builder.py",
        inject_line="import pymupdf",
        expected_contract_keyword="pymupdf",
        label="pymupdf into intelligence",
    ),
)


# For all C2* layer DAG violations the assertion uses the "c2" keyword,
# which appears in every c2a/c2b/c2c/c2d/c2e contract's human-readable name.
_C2_KEYWORD: Final[str] = "c2"


_LAYER_DAG_CASES: Final[tuple[ViolationCase, ...]] = (
    ViolationCase(
        target_rel_path="features/extraction/parsing/document_parser.py",
        inject_line=(
            "from app.features.extraction.coordinates.offset_index import OffsetIndex  # noqa: F401"
        ),
        expected_contract_keyword=_C2_KEYWORD,
        label="parsing -> coordinates (AC5)",
    ),
    ViolationCase(
        target_rel_path="features/extraction/annotation/pdf_annotator.py",
        inject_line=(
            "from app.features.extraction.intelligence.intelligence_provider "
            "import IntelligenceProvider  # noqa: F401"
        ),
        expected_contract_keyword=_C2_KEYWORD,
        label="annotation -> intelligence",
    ),
    ViolationCase(
        target_rel_path="features/extraction/schemas/extract_request.py",
        inject_line=(
            "from app.features.extraction.parsing.parsed_document "
            "import ParsedDocument  # noqa: F401"
        ),
        expected_contract_keyword=_C2_KEYWORD,
        label="schemas -> parsing",
    ),
)


def _assert_violation_caught(
    case: ViolationCase,
    tmp_path: Path,
) -> None:
    """Shared U7/U8 assertion: copy tree, inject, run linter, verify the right break."""
    app_tree = copy_app_tree(tmp_path)
    contracts = copy_contracts(tmp_path)

    target = app_tree / case.target_rel_path
    inject_import_line(target, case.inject_line)

    result = run_lint_imports(tmp_path, contracts)

    assert result.returncode != 0, (
        f"{case.label}: expected lint-imports to fail, "
        f"got exit {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    combined = (result.stdout + result.stderr).lower()
    normalized_output = combined.replace("\\", "/")
    assert case.expected_contract_keyword.lower() in normalized_output, (
        f"{case.label}: expected the broken contract for "
        f"'{case.expected_contract_keyword}' to be reported\n"
        f"OUTPUT:\n{result.stdout}\n{result.stderr}"
    )
    module_path = case.target_rel_path.replace("/", ".").removesuffix(".py").lower()
    assert module_path in normalized_output, (
        f"{case.label}: expected lint-imports to name the injected violator "
        f"'{module_path}'\nOUTPUT:\n{result.stdout}\n{result.stderr}"
    )


@pytest.mark.parametrize(
    "case",
    _THIRD_PARTY_CASES,
    ids=[c.label for c in _THIRD_PARTY_CASES],
)
def test_third_party_containment_catches_injected_violations(
    tmp_path: Path,
    case: ViolationCase,
) -> None:
    """U7: each forbidden-import carve-out is enforced in practice.

    Copies the app tree, injects one forbidden import into one file, runs
    lint-imports, and asserts the specific third-party containment contract
    is broken and the injected file is named as the violator.

    The `docling` case is the exact scenario called out in AC4. The others
    are extensions that prove the same pattern holds for every containment
    contract defined in the INI.
    """
    _assert_violation_caught(case, tmp_path)


@pytest.mark.parametrize(
    "case",
    _LAYER_DAG_CASES,
    ids=[c.label for c in _LAYER_DAG_CASES],
)
def test_layer_dag_catches_injected_illegal_edges(
    tmp_path: Path,
    case: ViolationCase,
) -> None:
    """U8: each illegal intra-feature edge is blocked by a C2 layers contract.

    The first case is the exact scenario called out in AC5 (parsing ->
    coordinates). The other cases extend the guarantee to adjacent DAG edges
    that the design spec Section 10 also forbids: annotation -> intelligence
    and schemas -> parsing.
    """
    _assert_violation_caught(case, tmp_path)


def test_clean_scratch_tree_passes_all_contracts(tmp_path: Path) -> None:
    """U9: the scratch-tree harness itself does not introduce violations.

    If this test fails, U7 and U8 could pass for the wrong reason - the
    builder would be injecting violations that mask the ones the tests mean
    to exercise. This test runs lint-imports on an unmodified copy of the
    app tree and asserts exit 0 + zero broken contracts.
    """
    app_tree = copy_app_tree(tmp_path)
    assert app_tree.exists(), "scratch-tree builder did not produce `app/`"
    contracts = copy_contracts(tmp_path)

    result = run_lint_imports(tmp_path, contracts)

    assert result.returncode == 0, (
        f"lint-imports unexpectedly broke on an unmodified scratch tree.\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "0 broken" in result.stdout.lower(), (
        f"expected lint-imports to report '0 broken' on a clean scratch tree; "
        f"the previous 'or \"broken\" not in ...' clause was permissive enough to "
        f"pass on degenerate output that never mentions 'broken' at all (issue #399)\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
