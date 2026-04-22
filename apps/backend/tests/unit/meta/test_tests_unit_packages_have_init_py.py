"""Guardrail that every ``tests/unit/*/`` subdirectory is a proper Python package.

Issue #395: the ``tests/unit/meta/`` directory shipped without an ``__init__.py``
even though every sibling test directory had one. Under pytest's rootdir-based
discovery this usually still works, but it breaks for any tooling that relies
on module-path imports (for example, pyright strict scans that include
``tests``, or helpers that walk ``Path(__file__).parents[N]`` expecting a fixed
layout). Issue #285 restored ``meta/__init__.py`` in passing via PR #311; this
meta-test pins the invariant so the same class of drift cannot recur silently.

The assertion is intentionally narrow:

- Walk every immediate subdirectory under ``apps/backend/tests/unit/``.
- Skip hidden directories (``.`` prefix) and ``__pycache__`` — neither are
  meaningful Python test packages.
- Every remaining subdirectory must contain a file named ``__init__.py``.

The test deliberately does NOT assert the file is empty or declare specific
subdirectories — the invariant is structural ("it's a package"), not editorial
("what's inside it"). New test directories are added regularly, and requiring
each one to be enumerated here would create a second source of truth that
would drift out of sync with the filesystem.
"""

from __future__ import annotations

from pathlib import Path

_TESTS_UNIT_ROOT = Path(__file__).resolve().parents[1]
_SKIPPED_DIRECTORY_NAMES = frozenset({"__pycache__"})


def _unit_test_package_dirs() -> list[Path]:
    """Return every immediate subdirectory of ``tests/unit/`` that should be a package.

    Skips hidden directories (names beginning with ``.``) and
    ``__pycache__``. The remaining directories are the ones that house test
    modules and therefore must be importable as packages.
    """
    return sorted(
        entry
        for entry in _TESTS_UNIT_ROOT.iterdir()
        if entry.is_dir()
        and not entry.name.startswith(".")
        and entry.name not in _SKIPPED_DIRECTORY_NAMES
    )


def test_tests_unit_root_is_discoverable() -> None:
    """Sanity-check that the walked path is the real ``tests/unit`` directory.

    If ``_TESTS_UNIT_ROOT`` ever drifts (for example, because this file is
    moved to a different depth in the tree), the subsequent assertion could
    silently pass against an unrelated directory. Anchor the walk to a known
    landmark — ``tests/unit/__init__.py`` itself — so a structural refactor
    turns this into a loud failure instead of a silent pass.
    """
    assert _TESTS_UNIT_ROOT.name == "unit", (
        f"Expected parents[1] to be the 'unit' directory, got "
        f"{_TESTS_UNIT_ROOT!s}. If this file has moved, update the "
        f"_TESTS_UNIT_ROOT parents[] index."
    )
    assert (_TESTS_UNIT_ROOT / "__init__.py").is_file(), (
        f"Expected {_TESTS_UNIT_ROOT}/__init__.py to exist as the package "
        f"anchor for the unit test tree."
    )


def test_every_tests_unit_subdir_has_init_py() -> None:
    """Every immediate subdirectory under tests/unit/ must contain __init__.py.

    See issue #395. This is the canonical invariant the guardrail protects:
    each test subpackage must be importable as a Python package so pyright
    strict scans, coverage tooling, and pathlib-based conftest helpers all
    resolve modules consistently.
    """
    missing = [
        subdir for subdir in _unit_test_package_dirs() if not (subdir / "__init__.py").is_file()
    ]
    assert not missing, (
        "The following tests/unit/*/ subdirectories are missing __init__.py. "
        "Create an empty __init__.py in each to match the sibling convention "
        "(see issue #395):\n  - "
        + "\n  - ".join(str(path.relative_to(_TESTS_UNIT_ROOT)) for path in missing)
    )
