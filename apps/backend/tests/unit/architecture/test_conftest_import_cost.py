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
from collections.abc import Iterator
from pathlib import Path
from typing import Final

from ._linter_subprocess import BACKEND_DIR

_CONFTEST_PATH: Final[Path] = BACKEND_DIR / "tests" / "conftest.py"


def _iter_module_scope_imports(tree: ast.Module) -> Iterator[ast.Import | ast.ImportFrom]:
    """Yield every Import/ImportFrom that binds a name at module scope.

    Descends into top-level compound statements (If/Try/TryStar/With/For/
    While/Match) because their bodies execute at module scope in Python —
    names bound inside them are visible at module scope. Function,
    async-function, and class bodies DO create their own lexical scopes and
    are skipped so a deliberately-lazy ``import heavy_mod`` inside a
    fixture body is not flagged by this gate (PR #524 review). Mirrors
    ``_iter_module_scope_imports`` in ``test_dynamic_import_containment.py``
    so the same descent policy applies to both containment gates.
    """
    try_star_type: type[ast.AST] | None = getattr(ast, "TryStar", None)
    stack: list[ast.stmt] = list(tree.body)
    while stack:
        node = stack.pop(0)
        if isinstance(node, ast.Import | ast.ImportFrom):
            yield node
            continue
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        if isinstance(node, ast.If):
            stack[:0] = list(node.body) + list(node.orelse)
        elif isinstance(node, ast.Try):
            handlers: list[ast.stmt] = [s for h in node.handlers for s in h.body]
            stack[:0] = list(node.body) + handlers + list(node.orelse) + list(node.finalbody)
        elif try_star_type is not None and isinstance(node, try_star_type):
            handlers = [s for h in node.handlers for s in h.body]  # type: ignore[attr-defined]  # TryStar exposes handlers like Try
            stack[:0] = (
                list(node.body)  # type: ignore[attr-defined]
                + handlers
                + list(node.orelse)  # type: ignore[attr-defined]
                + list(node.finalbody)  # type: ignore[attr-defined]
            )
        elif isinstance(node, ast.With | ast.AsyncWith):
            stack[:0] = list(node.body)
        elif isinstance(node, ast.For | ast.AsyncFor | ast.While):
            stack[:0] = list(node.body) + list(node.orelse)
        elif isinstance(node, ast.Match):
            stack[:0] = [s for c in node.cases for s in c.body]


def _collect_static_dotted_imports(source: str) -> set[str]:
    """Return every dotted module name referenced by a module-scope import."""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in _iter_module_scope_imports(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif node.module is not None and node.level == 0:
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


def test_collect_static_dotted_imports_skips_imports_inside_function_body() -> None:
    """Lazy imports inside a fixture body MUST NOT be flagged as module-scope.

    PR #524 review: the previous ``ast.walk`` implementation descended into
    function bodies, so a fixture that did ``import heavy_mod`` lazily
    inside its body was flagged even though the whole point of the lazy
    import is to avoid the module-scope tax. The module-scope visitor now
    used by ``_collect_static_dotted_imports`` correctly skips function and
    method bodies.
    """
    source = (
        "def fixture_maker() -> object:\n"
        "    import app.features.extraction.skills\n"
        "    return None\n"
    )
    assert "app.features.extraction.skills" not in _collect_static_dotted_imports(source)


def test_collect_static_dotted_imports_catches_module_scope_import() -> None:
    """A plain module-scope import IS still flagged (control case)."""
    source = "import app.features.extraction.skills\n"
    assert "app.features.extraction.skills" in _collect_static_dotted_imports(source)


def test_collect_static_dotted_imports_catches_conditional_module_scope_import() -> None:
    """A module-scope ``try: import X`` at top level IS flagged.

    Compound statements (``try``/``if``/``with``/``for``/``match``) don't
    introduce a new lexical scope in Python — a name bound inside their
    body is visible at module scope. So the visitor must descend into
    them, matching the policy of ``_iter_module_scope_imports`` in the
    sibling ``test_dynamic_import_containment.py``.
    """
    source = "try:\n    import app.features.extraction.skills\nexcept ImportError:\n    pass\n"
    assert "app.features.extraction.skills" in _collect_static_dotted_imports(source)


def test_collect_static_dotted_imports_skips_imports_inside_class_body() -> None:
    """Class-body imports are local to the class scope and MUST NOT be flagged."""
    source = "class _Holder:\n    import app.features.extraction.skills as _skills_mod\n"
    assert "app.features.extraction.skills" not in _collect_static_dotted_imports(source)
