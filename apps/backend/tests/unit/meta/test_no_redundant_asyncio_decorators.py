"""Guardrail: no test file under ``apps/backend/tests/`` may carry a
``@pytest.mark.asyncio`` decorator on an ``async def test_*`` function
while ``asyncio_mode = "auto"`` is set in ``apps/backend/pyproject.toml``.

Why (issue #290):

- ``apps/backend/pyproject.toml`` declares ``asyncio_mode = "auto"`` under
  ``[tool.pytest.ini_options]``. In auto mode, ``pytest-asyncio`` implicitly
  applies ``@pytest.mark.asyncio`` to every ``async def`` test function, so
  per-function decorators are no-ops today.
- The inconsistency between decorated and undecorated ``async def`` tests
  makes auditing painful — and if the project ever flips ``asyncio_mode``
  to ``"strict"``, the mismatch turns silently broken (some tests run,
  others do not).
- A single sweep removed the redundant decorators; this meta-test pins
  the invariant so reintroductions are caught at ``task check`` time
  instead of at a future mode flip.

This guard is intentionally **coupled** to ``asyncio_mode = "auto"``. If
``asyncio_mode`` is ever changed away from ``"auto"`` (issue #343), the
coupling assertion here will fail first and explicitly point the author
at this file — at which point the invariant must be revisited (per-function
decorators become load-bearing under ``"strict"`` mode).
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path
from typing import Final

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[5]
_BACKEND_ROOT: Final[Path] = _REPO_ROOT / "apps" / "backend"
_BACKEND_PYPROJECT: Final[Path] = _BACKEND_ROOT / "pyproject.toml"
_TESTS_ROOT: Final[Path] = _BACKEND_ROOT / "tests"


def _asyncio_mode_is_auto() -> bool:
    """Return True iff ``apps/backend/pyproject.toml`` sets asyncio_mode="auto"."""
    data = tomllib.loads(_BACKEND_PYPROJECT.read_text(encoding="utf-8"))
    tool = data.get("tool", {})
    pytest_cfg = tool.get("pytest", {}).get("ini_options", {})
    return pytest_cfg.get("asyncio_mode") == "auto"


def _decorator_is_pytest_mark_asyncio(decorator: ast.expr) -> bool:
    """Return True iff ``decorator`` is ``@pytest.mark.asyncio`` (with or without call).

    Matches both bare ``@pytest.mark.asyncio`` and a called form
    ``@pytest.mark.asyncio(...)``. Does NOT match ``@pytest.mark.asyncio_something``
    (attribute-prefix false-positive guard).
    """
    # Unwrap a Call node to its callee (so `@pytest.mark.asyncio()` also matches).
    node = decorator.func if isinstance(decorator, ast.Call) else decorator
    # Expected shape: Attribute(value=Attribute(value=Name("pytest"), attr="mark"), attr="asyncio")
    if not isinstance(node, ast.Attribute) or node.attr != "asyncio":
        return False
    mark_node = node.value
    if not isinstance(mark_node, ast.Attribute) or mark_node.attr != "mark":
        return False
    pytest_node = mark_node.value
    return isinstance(pytest_node, ast.Name) and pytest_node.id == "pytest"


def _offenders_in_file(path: Path) -> list[tuple[Path, int, str]]:
    """Return every ``@pytest.mark.asyncio`` site on ``async def test_*`` in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    rel = path.relative_to(_REPO_ROOT)
    return [
        (rel, decorator.lineno, node.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("test_")
        for decorator in node.decorator_list
        if _decorator_is_pytest_mark_asyncio(decorator)
    ]


def _find_redundant_decorators() -> list[tuple[Path, int, str]]:
    """Scan every ``tests/**/*.py`` file and collect ``@pytest.mark.asyncio``
    sites on ``async def test_*`` functions.

    Returns a list of ``(relative_path, line_number, function_name)`` tuples.
    """
    offenders: list[tuple[Path, int, str]] = []
    for path in sorted(_TESTS_ROOT.rglob("*.py")):
        offenders.extend(_offenders_in_file(path))
    return offenders


def test_asyncio_mode_is_auto_so_this_guard_applies() -> None:
    """Coupling assertion: if ``asyncio_mode`` ever stops being ``"auto"``,
    the rest of this file's assertions must be revisited.

    Under ``strict`` or ``legacy`` mode, ``@pytest.mark.asyncio`` becomes
    load-bearing rather than redundant, and the sweep / guard in this file
    would be actively wrong. Fail loudly at this assertion first so the
    author is pointed at the right place.
    """
    assert _asyncio_mode_is_auto(), (
        f"{_BACKEND_PYPROJECT} no longer sets asyncio_mode='auto'. The guard "
        f"in {Path(__file__).relative_to(_REPO_ROOT)} assumes auto mode; if the "
        f"mode has changed, reassess whether per-function "
        f"@pytest.mark.asyncio decorators are still redundant (issue #290)."
    )


def test_no_redundant_pytest_mark_asyncio_decorators_in_test_tree() -> None:
    """No ``async def test_*`` under ``apps/backend/tests/`` may carry
    ``@pytest.mark.asyncio``. Under ``asyncio_mode = "auto"`` the decorator
    is a no-op and only creates audit friction (issue #290).

    If this test fails, remove the listed decorators. Do NOT silence the
    test: the decorator becomes a silent trap the day ``asyncio_mode`` is
    flipped to ``"strict"`` (issue #343).
    """
    offenders = _find_redundant_decorators()
    if not offenders:
        return
    formatted = "\n".join(
        f"  - {path}:{lineno} on async def {name}" for path, lineno, name in offenders
    )
    msg = (
        "Found @pytest.mark.asyncio decorator(s) on async def test_* under "
        "apps/backend/tests/. Under asyncio_mode='auto' these decorators are "
        "redundant (pytest-asyncio applies the mark implicitly to every async "
        "def test). Remove them. Offending sites:\n"
        f"{formatted}\n"
        "See issue #290 for the rationale."
    )
    raise AssertionError(msg)
