"""Architecture gate: ``tests/conftest.py`` stays lazy w.r.t. extraction.

Issue #354: importing ``tests/conftest.py`` used to eagerly pull in
``app.features.extraction.skills`` (including deep-freeze mapping, skill
manifest validation, Skill / SkillExample / SkillDoclingConfig dataclasses)
for every test file pytest discovers -- unit tests that only touch
coordinates, schemas, or the core logger paid that load-time tax.

The fix moved ``make_skill`` and its extraction imports into an explicit
``tests/_support/skill_factory.py`` module that callers opt into. This
architecture test pins that invariant: ``tests.conftest`` must not
statically import from ``app.features.extraction.*`` at module scope, and
importing it at runtime must not load the extraction skills package
transitively.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path
from typing import Final

from ._linter_subprocess import BACKEND_DIR

_CONFTEST_PATH: Final[Path] = BACKEND_DIR / "tests" / "conftest.py"


def _collect_static_dotted_imports(source: str) -> set[str]:
    """Return every dotted module name referenced by a top-level import."""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            names.add(node.module)
    return names


def test_conftest_has_no_static_extraction_feature_imports() -> None:
    """``tests/conftest.py`` must not statically import from ``app.features.*``.

    Extraction-feature modules are heavy (deep-freeze validation, docling
    config dataclasses, skill manifest). Pulling them in at conftest scope
    forces every test file pytest collects to pay the load cost -- even
    unit tests that only touch coordinates or schemas. ``make_skill`` now
    lives in ``tests/_support/skill_factory.py``; callers import it
    explicitly from there.
    """
    source = _CONFTEST_PATH.read_text()
    dotted = _collect_static_dotted_imports(source)
    offending = {name for name in dotted if name.startswith("app.features.")}
    assert not offending, (
        "tests/conftest.py must not import from app.features.* at module scope; "
        f"found: {sorted(offending)!r} -- move heavy helpers into "
        "tests/_support/ and import from there at each call site (issue #354)"
    )


def test_importing_conftest_does_not_load_extraction_skills_package() -> None:
    """Importing ``tests.conftest`` must not transitively load the skills pkg.

    Guards against regression via a re-export / star-import that hides a
    heavy dependency behind an indirect module reference. Runs the import
    inside a subprocess with a clean ``sys.modules`` so the check is not
    confounded by sibling test modules that legitimately import the
    skills package via ``tests/_support/skill_factory.py``. A subprocess
    is the honest way to observe the transitive-import cost of
    ``tests.conftest`` in isolation.
    """
    script = (
        "import sys\n"
        "import importlib\n"
        "importlib.import_module('tests.conftest')\n"
        "leaked = sorted(\n"
        "    name for name in sys.modules\n"
        "    if name.startswith('app.features.extraction.skills')\n"
        ")\n"
        "print(repr(leaked))\n"
    )
    # The CLAUDE.md prohibition on ``os.environ`` targets reading config
    # values that belong behind pydantic-settings. Here it is only used to
    # inherit the parent process environment into the subprocess and prepend
    # ``BACKEND_DIR`` so the subprocess can resolve the ``tests.`` package
    # the same way pytest does via its ``pythonpath = ["."]`` setting.
    env = {
        **os.environ,
        "PYTHONPATH": f"{BACKEND_DIR}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
    }
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    leaked_repr = result.stdout.strip()
    assert leaked_repr == "[]", (
        "importing tests.conftest must not transitively load "
        f"app.features.extraction.skills; leaked modules: {leaked_repr} "
        "(issue #354)"
    )
