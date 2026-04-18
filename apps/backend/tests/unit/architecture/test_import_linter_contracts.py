"""Structural tests for apps/backend/architecture/import-linter-contracts.ini.

Covers scenarios U1-U6 from PDFX-E007-F004: the INI file parses cleanly,
every declared contract carries a rationale comment, contracts C1-C6 are
all present with the expected contract types, the scope-audit invariant
holds (no references to non-extraction feature packages), and the Taskfile
still wires `check:arch` into the top-level `check` target.

These tests treat the INI file and `Taskfile.yml` as static build artifacts;
they do not invoke `lint-imports`. The dynamic meta-enforcement tests live
in `test_contract_enforcement.py`, and the live subprocess runs live in
`tests/integration/architecture/test_import_linter_live.py`.
"""

from __future__ import annotations

import configparser
import re

import pytest
import yaml

from ._linter_subprocess import REAL_CONTRACTS_PATH, REPO_ROOT

_TASKFILE_PATH = REPO_ROOT / "Taskfile.yml"


# The stable keyword each contract section must advertise in its section name,
# plus the set of import-linter contract types that are valid for that rule.
# Matching is substring-based so the human-readable part of the contract id
# can be reworded without breaking the test.
#
# C2 (the intra-feature DAG) is allowed to span multiple contract types because
# the DAG has asymmetric cross-sibling edges (e.g. coordinates → extraction
# but not extraction → coordinates) that a single `layers` contract cannot
# express cleanly. The Open Questions section of PDFX-E007-F004 explicitly
# defaults to "use the right type per rule," so a C2 implementation that
# decomposes into a layers contract, multiple forbidden contracts, and/or an
# independence contract is spec-compliant.
_EXPECTED_CONTRACT_KEYWORDS: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("c1", "feature-independence", frozenset({"independence"})),
    ("c2", "extraction-layers", frozenset({"layers", "independence", "forbidden"})),
    ("c3", "docling", frozenset({"forbidden"})),
    ("c4", "pymupdf", frozenset({"forbidden"})),
    ("c5", "langextract", frozenset({"forbidden"})),
    ("c6", "httpx", frozenset({"forbidden"})),
)


@pytest.fixture(scope="module")
def contracts_parser() -> configparser.ConfigParser:
    """Return a ConfigParser with the contracts file loaded.

    Module-scoped because parsing is read-only and every test consumes it.
    """
    parser = configparser.ConfigParser()
    parser.read(REAL_CONTRACTS_PATH)
    return parser


@pytest.fixture(scope="module")
def contracts_raw_text() -> str:
    """Return the raw INI file text (for comment-presence checks)."""
    return REAL_CONTRACTS_PATH.read_text()


def test_ini_parses_cleanly_with_root_package_app(
    contracts_parser: configparser.ConfigParser,
) -> None:
    """U1: the INI file parses cleanly and declares `root_package = app`."""
    assert contracts_parser.has_section("importlinter"), (
        "import-linter-contracts.ini must have an [importlinter] root section"
    )
    assert contracts_parser.get("importlinter", "root_package") == "app"


def test_every_contract_section_has_preceding_comment(
    contracts_raw_text: str,
) -> None:
    """U2: every [importlinter:contract:*] section has a `#` comment above it.

    Walks the file line-by-line; for each contract header, scans upward past
    blank lines and asserts the preceding non-blank content is a comment line.
    configparser strips comments, so this check is run against raw text.
    """
    lines = contracts_raw_text.splitlines()
    contract_header = re.compile(r"^\[importlinter:contract:[^]]+\]\s*$")

    offenders: list[str] = []
    for idx, line in enumerate(lines):
        if not contract_header.match(line):
            continue
        cursor = idx - 1
        while cursor >= 0 and lines[cursor].strip() == "":
            cursor -= 1
        if cursor < 0 or not lines[cursor].lstrip().startswith("#"):
            offenders.append(line.strip())

    assert not offenders, (
        f"Contract sections without a preceding comment: {offenders}. "
        "AC6 requires every contract to carry a rationale comment."
    )


def test_all_expected_contracts_are_present_by_name(
    contracts_parser: configparser.ConfigParser,
) -> None:
    """U3: C1-C6 plus the preserved template `shared-no-features` contract exist."""
    section_names = [
        name for name in contracts_parser.sections() if name.startswith("importlinter:contract:")
    ]
    lowered = [name.lower() for name in section_names]

    assert any("shared-no-features" in name for name in lowered), (
        f"template `shared-no-features` contract must still exist: {section_names}"
    )

    for label, keyword, _ in _EXPECTED_CONTRACT_KEYWORDS:
        matches = [name for name in lowered if keyword in name]
        assert matches, (
            f"Expected {label} contract with keyword '{keyword}' in its id; "
            f"found only: {section_names}"
        )


def test_each_contract_uses_an_expected_type(
    contracts_parser: configparser.ConfigParser,
) -> None:
    """U4: each contract's `type` field matches the rule it encodes.

    C1 = independence (one feature package, no sibling imports).
    C2 = layers | independence | forbidden (the intra-feature DAG may be
         decomposed into multiple narrow contracts; each sub-contract must
         still use one of the three valid types for architectural rules).
    C3/C4/C5/C6 = forbidden (third-party containment).
    """
    for label, keyword, allowed_types in _EXPECTED_CONTRACT_KEYWORDS:
        matching = [
            name
            for name in contracts_parser.sections()
            if name.startswith("importlinter:contract:") and keyword in name.lower()
        ]
        assert matching, f"{label}: no section matched keyword '{keyword}'"
        for section in matching:
            assert contracts_parser.has_option(section, "type"), (
                f"{label} / {section}: missing `type` key"
            )
            actual_type = contracts_parser.get(section, "type").strip()
            assert actual_type in allowed_types, (
                f"{label} / {section}: expected type in {sorted(allowed_types)}, "
                f"got `{actual_type}`"
            )


def test_no_contract_references_a_non_extraction_feature_package(
    contracts_parser: configparser.ConfigParser,
) -> None:
    """U5: AC7 - every feature-scoped module referenced is under `app.features.extraction`.

    Parses every value in every section, tokenizes by whitespace, pipe, colon,
    and arrow, then asserts that any token starting with `app.features.` is
    followed by `extraction` and nothing else. Guarantees a future sibling
    feature can be added without editing this file.
    """
    token_splitter = re.compile(r"[\s|:>\->\n]+")
    offending: list[tuple[str, str, str]] = []

    for section in contracts_parser.sections():
        for key, value in contracts_parser.items(section):
            if key == "root_package":
                continue
            for raw_token in token_splitter.split(value):
                token = raw_token.strip()
                if not token or not token.startswith("app.features."):
                    continue
                remainder = token[len("app.features.") :]
                head = remainder.split(".", 1)[0]
                if head != "extraction":
                    offending.append((section, key, token))

    assert not offending, (
        "Contracts must only reference `app.features.extraction[.*]`. "
        f"Non-extraction references found: {offending}"
    )


def test_taskfile_wires_lint_imports_into_task_check() -> None:
    """U6: AC2/AC3 - `task check` reaches import-linter as a direct dependency.

    Pure YAML-parse + key-lookup assertion. Does not invoke the task runner.
    Verifies:
    - `check:arch` exists and invokes lint-imports.
    - `check` lists `check:arch` as a direct `task:` entry in its `cmds:`
      list (issue #215 — no transitive indirection via `lint`).

    The sibling hygiene test in `test_taskfile_check_hygiene.py` pins the
    broader invariant that every required gate is enumerated directly in
    `check`'s `cmds:` list. This test focuses specifically on the
    import-linter gate.
    """
    taskfile = yaml.safe_load(_TASKFILE_PATH.read_text())

    tasks = taskfile["tasks"]
    assert "check:arch" in tasks, (
        "Taskfile.yml must declare a `check:arch` task that runs import-linter"
    )

    arch_cmds = tasks["check:arch"]["cmds"]
    lint_imports_cmd = " ".join(str(cmd) for cmd in arch_cmds)
    assert "lint-imports" in lint_imports_cmd, (
        f"`check:arch` must invoke `lint-imports`, got: {arch_cmds}"
    )
    assert "architecture/import-linter-contracts.ini" in lint_imports_cmd, (
        "`check:arch` must point `lint-imports` at the contracts file"
    )

    check_cmds = tasks["check"]["cmds"]
    check_calls_arch = any(
        isinstance(cmd, dict) and cmd.get("task") == "check:arch" for cmd in check_cmds
    )
    assert check_calls_arch, (
        "`check` must include `check:arch` as a direct `task:` entry in its `cmds:` list "
        "(issue #215 — reaching it only via a sibling task hides the gate and lets a "
        "refactor silently drop it)"
    )
