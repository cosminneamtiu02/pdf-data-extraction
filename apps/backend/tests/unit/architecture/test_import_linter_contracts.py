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

import ast
import configparser
import re
from pathlib import Path

import pytest
import yaml

from ._linter_subprocess import BACKEND_DIR, REAL_CONTRACTS_PATH, REPO_ROOT

_TASKFILE_PATH = REPO_ROOT / "Taskfile.yml"
_APP_ROOT: Path = BACKEND_DIR / "app"


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


def test_third_party_containment_contracts_alert_on_unmatched_ignores(
    contracts_parser: configparser.ConfigParser,
) -> None:
    """C3-C6 (third-party containment) must set alerting on unmatched ignores.

    Rationale (issue #273): `ignore_imports` on a containment contract is a
    carve-out list saying "these specific files are allowed to import the
    forbidden third-party module." If the codebase later drops one of those
    edges (e.g. a refactor switches from static `import langextract` to a
    lazy `importlib.import_module` wrapper), the ignore becomes "unmatched"
    — it references an import that no longer exists — and import-linter
    silently accepts that, shrinking the contract's enforcement surface
    with no warning.

    `unmatched_ignore_imports_alerting = warn` turns that silent drift into
    a visible warning in `lint-imports` output. C3 (docling), C4 (pymupdf),
    and C6 (httpx) already set it. C5 (langextract) did not, until #273.
    This test enforces the parity across all four third-party containment
    contracts.
    """
    third_party_keywords = ("docling", "pymupdf", "langextract", "httpx")
    offenders: list[str] = []
    for section in contracts_parser.sections():
        if not section.startswith("importlinter:contract:"):
            continue
        if not any(keyword in section.lower() for keyword in third_party_keywords):
            continue
        if not contracts_parser.has_option(section, "ignore_imports"):
            continue
        alerting = contracts_parser.get(
            section, "unmatched_ignore_imports_alerting", fallback=""
        ).strip()
        if alerting != "warn":
            offenders.append(f"{section} (alerting={alerting!r})")

    assert not offenders, (
        "Every third-party containment contract (C3-C6) with `ignore_imports` "
        "must also set `unmatched_ignore_imports_alerting = warn` so stale "
        "carve-outs surface as warnings instead of silently shrinking "
        f"enforcement. Offenders: {offenders}"
    )


# ─────────────────────────────────────────────────────────────────────────
# Third-party containment: ignore-list entries must actually import the
# contained package (issue #394).
#
# `ignore_imports` on C3/C4/C5/C6 is a carve-out list saying "these
# specific files are allowed to import the forbidden third-party module."
# An entry that references a file which does NOT import the module is
# dead: it carves out a reference that does not exist. Today
# `unmatched_ignore_imports_alerting = warn` only produces a warning on
# `lint-imports` runs — and a warning is easy to miss in CI logs, and
# worse, the dead entry would silently re-activate if the file later
# starts importing the contained package for unrelated reasons (the
# ignore would pre-authorize an import the linter would otherwise
# forbid, defeating containment).
#
# The scanner treats BOTH static imports (`import docling` /
# `from docling.X import Y`) AND dynamic imports
# (`importlib.import_module("docling")` / `__import__("docling")` /
# `importlib.util.find_spec("docling")`) as references, because every
# containment file in this repo today uses lazy `importlib.import_module`
# to defer the heavy third-party import off the cold start path. The
# `ignore_imports` carve-outs are written proactively against those lazy
# imports so the contract stays green when a future refactor switches
# to static imports. A pure static-import check would false-fail every
# live entry today.
# ─────────────────────────────────────────────────────────────────────────

# Map import-linter contract keyword → root package name the contract forbids.
# Mirrors the `_CONTAINED_PACKAGES` map in `test_dynamic_import_containment.py`
# but keyed by contract keyword instead of package name, because this test
# walks the INI file section-by-section.
_CONTRACT_KEYWORD_TO_PACKAGES: dict[str, tuple[str, ...]] = {
    "docling": ("docling",),
    "pymupdf": ("pymupdf", "fitz"),
    "langextract": ("langextract",),
    "httpx": ("httpx",),
}


def _parse_ignore_imports(raw: str) -> list[tuple[str, str]]:
    """Parse a multi-line `ignore_imports` value into (source, target) pairs.

    The INI value is a newline-separated list of ``source -> target`` lines.
    configparser gives us the flattened text (with embedded newlines), so we
    split on newlines and then on the literal ``->`` arrow. Whitespace
    around both sides is stripped. Blank lines are skipped.
    """
    pairs: list[tuple[str, str]] = []
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "->" not in line:
            continue
        left, right = line.split("->", 1)
        pairs.append((left.strip(), right.strip()))
    return pairs


def _module_path_for(module_dotted: str) -> Path:
    """Resolve a ``app.features.extraction.parsing.foo`` dotted name to a file path.

    ``app`` maps to ``apps/backend/app/`` (see `_APP_ROOT`). Every segment after
    the ``app`` prefix becomes a directory component, with the final segment
    being the ``.py`` file. Returns the resolved file path even if it does not
    exist — the caller is responsible for checking existence and failing with
    a clear error message.
    """
    if not module_dotted.startswith("app."):
        msg = f"unexpected module prefix for ignore-imports source: {module_dotted!r}"
        raise ValueError(msg)
    remainder = module_dotted[len("app.") :]
    parts = remainder.split(".")
    return _APP_ROOT.joinpath(*parts[:-1], f"{parts[-1]}.py")


_DYNAMIC_IMPORT_CALLABLE_NAMES: frozenset[str] = frozenset(
    {"import_module", "find_spec", "__import__"},
)


def _is_dynamic_import_call(func: ast.expr) -> bool:
    """Return True iff `func` names a dynamic-import callable.

    Matches both the attribute forms (``importlib.import_module(...)``,
    ``importlib.util.find_spec(...)``) and the bare-name forms
    (``import_module(...)``, ``__import__(...)``, ``find_spec(...)``)
    without tracking import aliases, because this helper is intentionally
    permissive — the question is "does this file reference the package
    through ANY plausible import mechanism," not "is this a properly-guarded
    containment point" (the latter is the job of
    `test_dynamic_import_containment.py`). A false positive here would only
    matter if a file binds a different callable to a name like
    `import_module`, which is vanishingly unlikely in production code.
    """
    if isinstance(func, ast.Attribute):
        return func.attr in _DYNAMIC_IMPORT_CALLABLE_NAMES
    if isinstance(func, ast.Name):
        return func.id in _DYNAMIC_IMPORT_CALLABLE_NAMES
    return False


def _first_string_arg(node: ast.Call) -> str | None:
    """Return the first positional or ``name=`` kwarg as a string literal, else None.

    Mirrors the keyword-fallback logic from
    ``_collect_dynamic_import_targets`` in `test_dynamic_import_containment.py`
    — ``importlib.import_module(name="docling")`` and
    ``importlib.util.find_spec(name="docling")`` are both canonical and must
    not silently evade the gate.
    """
    arg: ast.expr | None = None
    if node.args:
        arg = node.args[0]
    else:
        for kw in node.keywords:
            if kw.arg == "name":
                arg = kw.value
                break
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    return None


def _target_matches_any_package(target: str, packages: tuple[str, ...]) -> bool:
    """Return True iff `target` equals any package or is a submodule of one.

    Matches `pkg` itself and any `pkg.X.Y` dotted child, but not lookalike
    names like `pkgish`. Same rule as ``_target_matches_package`` in
    `test_dynamic_import_containment.py`.
    """
    return any(target == pkg or target.startswith(pkg + ".") for pkg in packages)


def _module_references_package(source: str, packages: tuple[str, ...]) -> bool:
    """Return True iff `source` statically or dynamically references any package.

    Walks the parsed AST once and checks:
      - ``import <pkg>`` / ``import <pkg>.X`` (static).
      - ``from <pkg>[.X] import ...`` (static).
      - ``importlib.import_module("<pkg>[.X]")`` / ``__import__("<pkg>[.X]")``
        / ``importlib.util.find_spec("<pkg>[.X]")`` (dynamic, string literal).

    A dotted target like ``"docling.datamodel.base_models"`` matches
    ``"docling"`` via prefix boundary (same rule as
    `_target_matches_package` in `test_dynamic_import_containment.py`).

    We reimplement the minimal AST walk here (rather than import from
    `test_dynamic_import_containment`) because that module's helpers target a
    different use case (narrow false-positive filtering for the containment
    gate) and importing across test modules creates a coupling this test does
    not need — we want to accept any plausible reference to the package,
    including module-scope dynamic imports without alias tracking, because
    the question here is "is this entry doing SOMETHING with the package?"
    not "is this a properly-guarded containment point?"
    """
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(_target_matches_any_package(alias.name, packages) for alias in node.names):
                return True
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.level == 0
            and _target_matches_any_package(node.module, packages)
        ):
            return True
        elif isinstance(node, ast.Call) and _is_dynamic_import_call(node.func):
            arg_value = _first_string_arg(node)
            if arg_value is not None and _target_matches_any_package(arg_value, packages):
                return True
    return False


def _collect_third_party_ignore_entries(
    contracts_parser: configparser.ConfigParser,
) -> list[tuple[str, str, str, tuple[str, ...]]]:
    """Return (section, contract_keyword, source_module, packages) for every ignore entry.

    Walks every ``importlinter:contract:*`` section whose id contains one of
    the third-party keywords (docling/pymupdf/langextract/httpx), parses its
    ``ignore_imports`` multi-line value, and yields one tuple per ``source ->
    target`` line. The ``target`` side is not returned because every C3/C4/
    C5/C6 entry has a third-party target that matches the contract's
    `forbidden_modules`; the source is what we need to resolve to a file.
    """
    entries: list[tuple[str, str, str, tuple[str, ...]]] = []
    for section in contracts_parser.sections():
        if not section.startswith("importlinter:contract:"):
            continue
        lowered = section.lower()
        matched_keyword: str | None = next(
            (kw for kw in _CONTRACT_KEYWORD_TO_PACKAGES if kw in lowered),
            None,
        )
        if matched_keyword is None:
            continue
        if not contracts_parser.has_option(section, "ignore_imports"):
            continue
        raw = contracts_parser.get(section, "ignore_imports")
        packages = _CONTRACT_KEYWORD_TO_PACKAGES[matched_keyword]
        for source_module, _target in _parse_ignore_imports(raw):
            entries.append((section, matched_keyword, source_module, packages))
    return entries


def test_third_party_ignore_list_entries_actually_reference_their_package(
    contracts_parser: configparser.ConfigParser,
) -> None:
    """Issue #394: every file listed in a C3-C6 `ignore_imports` entry must
    actually reference the forbidden third-party package.

    An ignore entry like
    ``app.features.extraction.parsing._flat_docling_text_item -> docling`` is
    "dead" if `_flat_docling_text_item.py` contains no static or dynamic
    reference to the `docling` package. Dead entries silently mask future
    regressions: `unmatched_ignore_imports_alerting = warn` only emits a
    warning (easy to miss in CI logs), and if the file later starts
    importing docling for any reason, the dead ignore pre-authorizes the
    import the containment contract would otherwise forbid.

    This test walks every third-party containment contract's `ignore_imports`
    list and for each source module, reads the file and asserts it
    references the forbidden package via at least one mechanism:
      - static ``import <pkg>[.X]`` / ``from <pkg>[.X] import ...``
      - dynamic ``importlib.import_module("<pkg>[.X]")`` /
        ``__import__("<pkg>[.X]")`` /
        ``importlib.util.find_spec("<pkg>[.X]")``

    Both static and dynamic forms count because every containment file in
    this repo today uses lazy `importlib.import_module` to defer the heavy
    third-party import off the cold start path, and the carve-outs are
    written proactively to stay green when a refactor switches to static
    imports.
    """
    dead_entries: list[str] = []
    missing_files: list[str] = []
    for section, _keyword, source_module, packages in _collect_third_party_ignore_entries(
        contracts_parser
    ):
        file_path = _module_path_for(source_module)
        if not file_path.exists():
            missing_files.append(f"{section}: {source_module} -> {file_path} (file missing)")
            continue
        source = file_path.read_text(encoding="utf-8")
        if not _module_references_package(source, packages):
            dead_entries.append(
                f"{section}: {source_module} is in `ignore_imports` but the file "
                f"contains no static or dynamic reference to {packages}"
            )

    assert not missing_files, (
        "`ignore_imports` entry references a module whose source file does not exist. "
        "Either the file was deleted (remove the ignore) or the dotted name is wrong.\n"
        + "\n".join(f"  - {m}" for m in missing_files)
    )
    assert not dead_entries, (
        "Dead `ignore_imports` entries found: the file does not actually import the "
        "contained package. Remove the ignore — leaving it in place silently masks "
        "future regressions (a warning is not a gate, and the ignore would pre-"
        "authorize any future import the containment contract would otherwise "
        "forbid). See issue #394.\n" + "\n".join(f"  - {d}" for d in dead_entries)
    )


def test_dead_ignore_detector_catches_synthetic_non_importing_source() -> None:
    """Regression: the `_module_references_package` predicate must reject a
    file that does not reference the package.

    Pairs with `test_third_party_ignore_list_entries_actually_reference_their_package`:
    the filesystem walk could pass vacuously if `_module_references_package`
    silently returns True for everything. This synthetic check asserts the
    predicate correctly says "no reference" for a plain dataclass file like
    `_flat_docling_text_item.py` (the exact shape that motivated issue #394).
    If someone weakens the predicate, this test fails even though the
    filesystem walk stays green.
    """
    plain_dataclass_source = (
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "\n"
        "@dataclass(frozen=True)\n"
        "class FlatDoclingTextItem:\n"
        "    text: str\n"
        "    page_number: int\n"
    )
    assert not _module_references_package(plain_dataclass_source, ("docling",)), (
        "predicate falsely claimed a plain dataclass source references `docling`; "
        "dead-entry detection is broken"
    )


def test_dead_ignore_detector_accepts_static_and_dynamic_references() -> None:
    """Regression: the predicate must fire on every supported import mechanism.

    Covers the shapes used by real C3-C6 containment files today:
      - static ``import docling``
      - static ``from docling.X import Y``
      - dynamic ``importlib.import_module("docling.X")``
      - dynamic ``__import__("docling")``
      - dynamic ``importlib.util.find_spec("docling")``

    If the predicate regresses to (for example) only checking static imports,
    every live C3 entry would fail the filesystem walk because all three C3
    files use lazy `importlib.import_module`. This regression test catches
    that class of predicate weakening.
    """
    # Static `import <pkg>`
    assert _module_references_package("import docling\n", ("docling",))
    # Static `from <pkg>.X import Y`
    assert _module_references_package(
        "from docling.datamodel.base_models import Something\n", ("docling",)
    )
    # Dynamic attribute form
    assert _module_references_package(
        'import importlib\nimportlib.import_module("docling.datamodel")\n', ("docling",)
    )
    # Dynamic builtin form
    assert _module_references_package('__import__("docling")\n', ("docling",))
    # Dynamic find_spec
    assert _module_references_package(
        'import importlib.util\nimportlib.util.find_spec("docling")\n', ("docling",)
    )
    # Negative control: reference to a different package must NOT match.
    assert not _module_references_package("import pymupdf\n", ("docling",))


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
