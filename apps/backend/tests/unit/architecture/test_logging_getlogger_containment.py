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


def _file_calls_logging_getlogger(py_file: Path) -> bool:
    """Return True iff the file invokes ``logging.getLogger(...)`` as a real call.

    Parses the file's AST and looks for any ``Call`` node whose function is
    the attribute access ``logging.getLogger``. This deliberately skips:

    - Matches inside comments or docstrings (the rule text itself mentions
      the forbidden pattern and must not self-trigger the assertion).
    - Other ``getLogger`` calls that are not on the top-level ``logging``
      module (e.g. ``structlog.stdlib.get_logger`` is unrelated).
    """
    tree = ast.parse(py_file.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "getLogger"
            and isinstance(func.value, ast.Name)
            and func.value.id == "logging"
        ):
            return True
    return False


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
