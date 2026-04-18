"""Architecture: stdlib ``logging.getLogger`` is quarantined to a single central file.

CLAUDE.md's forbidden-patterns list states unconditionally:
"Never use ``logging.getLogger``. Use structlog."

The rule exists because every such call is a stdlib logger boot-up that
must route through the structlog bridge or else bypass the redaction /
ProcessorFormatter chain entirely. The one legitimate exception is the
structlog bootstrap itself in ``app/core/logging.py``, which installs the
root handler and suppresses noisy third-party loggers via a central helper.

This test is the enforcement gate for issue #210: exactly one file under
``apps/backend/app/`` is allowed to *call* ``logging.getLogger``, and that
file is ``app/core/logging.py``. New offenders must route through the
``silence_stdlib_logger(name, level)`` helper in that file instead of
grabbing the stdlib logger directly.

The check is AST-based (not a literal substring grep) so that documentation
strings that MENTION the pattern — including the rule itself — do not get
flagged. Only real call sites count as violations.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

from ._linter_subprocess import BACKEND_DIR

_APP_ROOT: Final[Path] = BACKEND_DIR / "app"
_ALLOWED_FILE: Final[Path] = _APP_ROOT / "core" / "logging.py"


def _collect_logging_bindings(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Walk import nodes and return (logging-module aliases, getLogger aliases).

    Always seeds the module-alias set with the canonical name ``"logging"``
    so a fully-qualified ``logging.getLogger`` call without a matching
    ``import logging`` (e.g. injected via dynamic mechanisms) is still
    treated as a binding to the stdlib module.
    """
    logging_module_aliases: set[str] = {"logging"}
    getlogger_direct_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "logging":
                    logging_module_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "logging":
            for alias in node.names:
                if alias.name == "getLogger":
                    getlogger_direct_aliases.add(alias.asname or alias.name)
    return logging_module_aliases, getlogger_direct_aliases


def _call_invokes_getlogger(
    node: ast.Call,
    logging_module_aliases: set[str],
    getlogger_direct_aliases: set[str],
) -> bool:
    """Return True iff ``node`` is a ``logging.getLogger``-equivalent call.

    Recognises both syntactic shapes:
    - ``<module-alias>.getLogger(...)`` — the standard form (or its
      ``import logging as lg; lg.getLogger(...)`` aliased variant).
    - ``<getLogger-alias>(...)`` — the ``from logging import getLogger``
      bare-name form (or its ``... as gl`` aliased variant).
    """
    func = node.func
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "getLogger"
        and isinstance(func.value, ast.Name)
        and func.value.id in logging_module_aliases
    ):
        return True
    return isinstance(func, ast.Name) and func.id in getlogger_direct_aliases


def _file_calls_logging_getlogger(py_file: Path) -> bool:
    """Return True iff the file invokes ``logging.getLogger(...)`` in any form.

    Parses the file's AST in two passes:

    1. **Binding pass** -- walk ``Import`` and ``ImportFrom`` nodes to build
       the set of local names that resolve to the stdlib ``logging`` module
       and the set that resolve directly to ``logging.getLogger``. This
       handles aliased imports such as ``import logging as lg`` or
       ``from logging import getLogger as gl``.

    2. **Call pass** -- walk ``Call`` nodes and flag two violation shapes:
       - ``<logging-alias>.getLogger(...)`` (e.g. ``logging.getLogger`` or
         ``lg.getLogger`` after ``import logging as lg``).
       - ``<getLogger-alias>(...)`` (e.g. ``getLogger`` or ``gl`` after
         ``from logging import getLogger as gl``).

    This deliberately skips:

    - Matches inside comments or docstrings (the rule text itself mentions
      the forbidden pattern and must not self-trigger the assertion).
    - Other ``getLogger`` calls that are not bound to the stdlib ``logging``
      module (e.g. ``structlog.stdlib.get_logger`` is unrelated).

    ``encoding="utf-8"`` is explicit so the gate behaves identically across
    platforms whose default text encoding is not UTF-8 (e.g. Windows
    cp1252). ``filename=str(py_file)`` is passed to ``ast.parse`` so any
    ``SyntaxError`` reports the offending file path.
    """
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    module_aliases, getlogger_aliases = _collect_logging_bindings(tree)
    return any(
        isinstance(node, ast.Call)
        and _call_invokes_getlogger(node, module_aliases, getlogger_aliases)
        for node in ast.walk(tree)
    )


def test_only_core_logging_py_uses_logging_getlogger() -> None:
    """Exactly one file under app/ may call ``logging.getLogger``; that file is app/core/logging.py.

    AST-scan the app/ tree for ``logging.getLogger`` call nodes. The only
    permitted match is ``app/core/logging.py``, which houses the structlog
    stdlib-bridge bootstrap plus the ``silence_stdlib_logger`` helper used
    to suppress noisy third-party loggers (docling, httpx, httpcore, ...).
    """
    offenders: list[str] = []
    for py_file in _APP_ROOT.rglob("*.py"):
        if py_file == _ALLOWED_FILE:
            continue
        if _file_calls_logging_getlogger(py_file):
            offenders.append(str(py_file.relative_to(_APP_ROOT)))

    assert not offenders, (
        "CLAUDE.md forbids ``logging.getLogger`` outside the central bootstrap "
        "in ``app/core/logging.py``. Offender(s) must route through "
        "``silence_stdlib_logger(name, level)`` instead:\n"
        + "\n".join(f"  - {o}" for o in offenders)
    )


def test_central_logging_module_still_calls_logging_getlogger() -> None:
    """Sanity check: the allow-listed file is actually where the helper lives.

    Guards against a future refactor silently deleting the helper or moving
    it out of ``app/core/logging.py``. If this assertion fails, the
    allow-list in ``test_only_core_logging_py_uses_logging_getlogger`` is
    stale and the quarantine boundary has drifted.
    """
    assert _file_calls_logging_getlogger(_ALLOWED_FILE), (
        f"{_ALLOWED_FILE} no longer calls ``logging.getLogger``. If the "
        "helper was relocated, update the allow-list in "
        "``test_only_core_logging_py_uses_logging_getlogger`` accordingly."
    )


def test_detector_catches_aliased_module_import(tmp_path: Path) -> None:
    """Detector must flag ``import logging as X; X.getLogger(...)`` bypass."""
    src = tmp_path / "aliased_module.py"
    src.write_text(
        "import logging as lg\nlg.getLogger('foo')\n",
        encoding="utf-8",
    )
    assert _file_calls_logging_getlogger(src), (
        "detector failed to catch `import logging as lg; lg.getLogger(...)` bypass form"
    )


def test_detector_catches_from_import_getlogger(tmp_path: Path) -> None:
    """Detector must flag ``from logging import getLogger; getLogger(...)`` bypass."""
    src = tmp_path / "from_import.py"
    src.write_text(
        "from logging import getLogger\ngetLogger('foo')\n",
        encoding="utf-8",
    )
    assert _file_calls_logging_getlogger(src), (
        "detector failed to catch `from logging import getLogger; getLogger(...)` bypass form"
    )


def test_detector_catches_from_import_getlogger_aliased(tmp_path: Path) -> None:
    """Detector must flag ``from logging import getLogger as gl; gl(...)`` bypass."""
    src = tmp_path / "from_import_aliased.py"
    src.write_text(
        "from logging import getLogger as gl\ngl('foo')\n",
        encoding="utf-8",
    )
    assert _file_calls_logging_getlogger(src), (
        "detector failed to catch `from logging import getLogger as gl; gl(...)` bypass form"
    )


def test_detector_does_not_flag_unrelated_getlogger(tmp_path: Path) -> None:
    """Detector must NOT flag a ``getLogger`` call bound to a non-logging module.

    Guards against false positives from third-party libraries that also
    expose a ``getLogger`` name (the rule is about the stdlib ``logging``
    module specifically, not any callable named ``getLogger``).
    """
    src = tmp_path / "unrelated.py"
    src.write_text(
        "import structlog\n"
        "structlog.stdlib.get_logger('foo')\n"
        "def getLogger(name): return name\n"
        "getLogger('bar')\n",
        encoding="utf-8",
    )
    assert not _file_calls_logging_getlogger(src), (
        "detector raised a false positive on a non-logging `getLogger` symbol"
    )
