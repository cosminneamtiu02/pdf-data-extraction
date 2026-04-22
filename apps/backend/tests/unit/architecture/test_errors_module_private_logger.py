"""Architecture gate: `app/api/errors.py` binds the module logger as `_logger`, not `logger`.

Sacred Rule #3 (no paradigm drift) in CLAUDE.md requires one way to do each
thing. Every other module in the backend binds its structlog logger as a
private `_logger` symbol (see `access_log_middleware.py`,
`probe_cache.py`, `upload_size_limit_middleware.py`, `main.py`). Issue #368
identified `app/api/errors.py` as the lone outlier: it used a public
`logger` binding, which invites other modules to `from app.api.errors
import logger` and spreads the module-logger pattern beyond its owner.

This test pins the invariant by AST-scanning `errors.py` for module-level
`Assign` / `AnnAssign` nodes and asserting no target is named `logger`. A
future refactor that reintroduces the public form (or forgets to rename
the binding after copy-pasting from a feature slice) fails this test
deterministically — a substring grep on the file would also match
docstrings / comments that merely *mention* the name, hence the AST walk.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

from ._linter_subprocess import BACKEND_DIR

_ERRORS_MODULE: Final[Path] = BACKEND_DIR / "app" / "api" / "errors.py"


def _module_level_assign_targets(tree: ast.Module) -> list[str]:
    """Return every name bound at module scope by `Assign` / `AnnAssign` nodes.

    Walks only the immediate body of the module — nested scopes (function
    bodies, class bodies, `if TYPE_CHECKING:` blocks, etc.) are intentionally
    skipped so that a local `logger = ...` inside a helper never accidentally
    masks the real top-level check. Tuple-unpacking targets (`a, b = ...`) are
    flattened so each bound name is surfaced independently.
    """
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                names.extend(_names_in_target(target))
        elif isinstance(node, ast.AnnAssign):
            names.extend(_names_in_target(node.target))
    return names


def _names_in_target(target: ast.expr) -> list[str]:
    """Flatten a single assignment target into the concrete names it binds.

    Handles three shapes: plain `Name` (`x = ...`), `Tuple` / `List`
    destructuring (`a, b = ...`), and nested combinations thereof. Non-name
    targets (`self.x = ...`, `d[k] = ...`) contribute no module-level name
    and are skipped.
    """
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Tuple | ast.List):
        out: list[str] = []
        for elt in target.elts:
            out.extend(_names_in_target(elt))
        return out
    return []


def test_errors_module_does_not_define_public_logger() -> None:
    """`app/api/errors.py` must not bind a public top-level `logger` symbol.

    CLAUDE.md Sacred Rule #3 forbids paradigm drift. Every other backend
    module uses `_logger = structlog.get_logger(__name__)`. `errors.py`
    must follow the same convention; the public form invites cross-module
    imports of a symbol that should be private to its defining module.
    """
    tree = ast.parse(_ERRORS_MODULE.read_text(encoding="utf-8"), filename=str(_ERRORS_MODULE))
    top_level_names = _module_level_assign_targets(tree)
    assert "logger" not in top_level_names, (
        f"{_ERRORS_MODULE} binds a public `logger` at module scope, "
        "breaking Sacred Rule #3 (no paradigm drift). Rename to `_logger` "
        "to match every other backend module (issue #368)."
    )


def test_errors_module_defines_private_logger() -> None:
    """Sanity check: `_logger` is the actual binding that replaced the public form.

    Guards against an accidental total deletion of the module logger (the
    "fix" shouldn't remove the log calls, just rename the binding). If this
    assertion fails, the rename drifted into a deletion.
    """
    tree = ast.parse(_ERRORS_MODULE.read_text(encoding="utf-8"), filename=str(_ERRORS_MODULE))
    top_level_names = _module_level_assign_targets(tree)
    assert "_logger" in top_level_names, (
        f"{_ERRORS_MODULE} must bind the module logger as `_logger` at "
        "module scope. If the rename was intentional to a different name, "
        "update this test accordingly."
    )
