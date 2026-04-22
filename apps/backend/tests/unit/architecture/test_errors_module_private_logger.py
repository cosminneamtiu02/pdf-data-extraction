"""Architecture gate: `app/api/errors.py` binds the module logger as `_logger`, not `logger`.

Sacred Rule #3 (no paradigm drift) in CLAUDE.md requires one way to do each
thing. Every other module in the backend binds its structlog logger as a
private `_logger` symbol (see `access_log_middleware.py`,
`probe_cache.py`, `upload_size_limit_middleware.py`, `main.py`). Issue #368
identified `app/api/errors.py` as the lone outlier: it used a public
`logger` binding, which invites other modules to `from app.api.errors
import logger` and spreads the module-logger pattern beyond its owner.

This test pins the invariant by AST-scanning `errors.py` for every name
bound at module scope — including `Assign` / `AnnAssign` targets *and*
`Import` / `ImportFrom` `as`-bindings, descending through top-level
compound statements (`if`/`try`/`with`/`for`/`match`) whose bodies execute
at module scope without creating a new lexical scope. A future refactor
that reintroduces the public form (or forgets to rename the binding after
copy-pasting from a feature slice) fails this test deterministically — a
substring grep on the file would also match docstrings / comments that
merely *mention* the name, hence the AST walk.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
from typing import Final

from ._linter_subprocess import BACKEND_DIR

_ERRORS_MODULE: Final[Path] = BACKEND_DIR / "app" / "api" / "errors.py"


def _iter_module_scope_statements(tree: ast.Module) -> Iterator[ast.stmt]:
    """Yield every statement whose execution binds names at module scope.

    Descends into compound statements (If/Try/TryStar/With/For/While/Match)
    whose bodies execute at module scope, but NOT into FunctionDef/
    AsyncFunctionDef/ClassDef, where names bind inside a new lexical scope
    and therefore are not module-level bindings.

    Mirrors ``_iter_module_scope_imports`` in
    ``test_dynamic_import_containment.py``: the same traversal shape is
    load-bearing for every module-scope invariant this test suite pins.
    Closing the review-#508 gap where a module-scope ``logger = ...`` wrapped
    in an ``if TYPE_CHECKING`` / ``try: ... except:`` block would silently
    bypass the gate.

    Python 3.11 added ``try*`` (``ast.TryStar``) and 3.10 added
    ``match``/``case`` (``ast.Match``). Both run their bodies at module
    scope when used at the top level, so they must be descended into as
    well — missing either leaves a bypass for the rename gate. ``ast.TryStar``
    is probed via ``getattr`` because older Pythons lack the attribute;
    the fallback is to skip the check cleanly rather than hard-import a
    symbol that may not exist.
    """
    try_star_type: type[ast.AST] | None = getattr(ast, "TryStar", None)
    stack: list[ast.stmt] = list(tree.body)
    while stack:
        node = stack.pop(0)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # New lexical scope; don't descend. Any ``logger = ...`` inside
            # a helper or class body is not a module-level binding.
            continue
        if isinstance(node, ast.If):
            stack[:0] = list(node.body) + list(node.orelse)
            continue
        if isinstance(node, ast.Try):
            handlers_body: list[ast.stmt] = [s for h in node.handlers for s in h.body]
            stack[:0] = list(node.body) + handlers_body + list(node.orelse) + list(node.finalbody)
            continue
        if try_star_type is not None and isinstance(node, try_star_type):
            # ``ast.TryStar`` (Python 3.11+) mirrors ``ast.Try`` — same
            # body/handlers/orelse/finalbody shape, so descend identically.
            handlers_body = [s for h in node.handlers for s in h.body]  # type: ignore[attr-defined]  # TryStar mirrors Try's shape
            stack[:0] = (
                list(node.body)  # type: ignore[attr-defined]
                + handlers_body
                + list(node.orelse)  # type: ignore[attr-defined]
                + list(node.finalbody)  # type: ignore[attr-defined]
            )
            continue
        if isinstance(node, (ast.With, ast.AsyncWith)):
            stack[:0] = list(node.body)
            continue
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            stack[:0] = list(node.body) + list(node.orelse)
            continue
        if isinstance(node, ast.Match):
            # match/case bodies (Python 3.10+) execute at module scope when
            # used at the top level. Descend into every case's body.
            cases_body: list[ast.stmt] = [s for c in node.cases for s in c.body]
            stack[:0] = cases_body
            continue
        yield node


def _module_level_assign_targets(tree: ast.Module) -> list[str]:
    """Return every name bound at module scope.

    Covers four binding forms that all land in the module namespace:

    * ``Assign``   — ``logger = ...``
    * ``AnnAssign`` — ``logger: Logger = ...``
    * ``Import``   — ``import logger`` and ``import structlog as logger``
    * ``ImportFrom`` — ``from structlog import get_logger as logger``

    Descends into top-level compound statements (``if``/``try``/``with``/
    ``for``/``while``/``match``) whose bodies execute at module scope
    without creating a new lexical scope. Function, async-function, and
    class bodies DO create their own scopes and are correctly skipped so
    that a local ``logger = ...`` inside a helper never accidentally masks
    the real top-level check. Tuple-unpacking targets (``a, b = ...``) are
    flattened so each bound name is surfaced independently.
    """
    names: list[str] = []
    for node in _iter_module_scope_statements(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                names.extend(_names_in_target(target))
        elif isinstance(node, ast.AnnAssign):
            names.extend(_names_in_target(node.target))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                # ``import a.b.c`` binds ``a`` in the module namespace;
                # ``import a.b.c as x`` binds ``x``. The first component
                # of the dotted name is the actual module-scope binding
                # when no alias is present.
                if alias.asname is not None:
                    names.append(alias.asname)
                else:
                    names.append(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            names.extend(
                alias.asname if alias.asname is not None else alias.name for alias in node.names
            )
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
    if isinstance(target, (ast.Tuple, ast.List)):
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


# ---------------------------------------------------------------------------
# Self-tests for the AST visitor. PR #508 Copilot review surfaced that the
# original ``_module_level_assign_targets`` only looked at ``tree.body`` and
# only handled ``Assign``/``AnnAssign`` — so a module-scope ``logger = ...``
# nested under an ``if``/``try``/``with``/``for``/``match`` statement would
# silently bypass the gate, and ``import structlog as logger`` at module
# scope was not considered at all. The tests below pin the broadened
# behaviour against synthetic module ASTs so that future refactors of the
# visitor cannot re-introduce either bypass.
# ---------------------------------------------------------------------------


def _parse(src: str) -> ast.Module:
    """Tiny helper: parse a heredoc-style Python source snippet into an AST.

    Keeps the self-tests below terse and readable — every one of them is
    "given this synthetic module body, does the visitor see the right
    module-scope bindings?" — by removing the ``ast.parse`` boilerplate.
    """
    return ast.parse(src)


def test_module_level_assign_targets_detects_plain_toplevel_logger() -> None:
    """Baseline: a plain module-scope ``logger = ...`` must still be caught.

    Regression guard for the refactor: the broadened visitor must not lose
    the original invariant (direct ``tree.body`` detection) while gaining
    nested-compound-statement descent.
    """
    tree = _parse("logger = object()\n")
    assert "logger" in _module_level_assign_targets(tree)


def test_module_level_assign_targets_descends_into_if_block() -> None:
    """A module-scope ``logger = ...`` nested under ``if`` must be caught.

    ``if`` at module scope does not create a new lexical scope, so the
    binding lands in the module namespace and is reachable via
    ``from module import logger``. The pre-fix visitor only walked
    ``tree.body`` and therefore missed this form — a real-world refactor
    that wrote ``if TYPE_CHECKING: logger = ...`` at module scope would
    have silently bypassed the gate.
    """
    tree = _parse("if True:\n    logger = object()\n")
    assert "logger" in _module_level_assign_targets(tree)


def test_module_level_assign_targets_descends_into_try_block() -> None:
    """A module-scope ``logger = ...`` nested under ``try`` must be caught.

    Same rationale as the ``if`` case: ``try`` at module scope runs its
    body and handlers in the module namespace, so any binding inside them
    is a module-scope binding. The excepted-fallback idiom
    (``try: import a as logger; except ImportError: import b as logger``)
    is a common real-world shape this covers.
    """
    tree = _parse("try:\n    logger = object()\nexcept Exception:\n    logger = None\n")
    assert "logger" in _module_level_assign_targets(tree)


def test_module_level_assign_targets_descends_into_with_block() -> None:
    """A module-scope ``logger = ...`` nested under ``with`` must be caught.

    ``with`` at module scope shares the module namespace; any binding
    inside its body is a module-scope binding. Uncommon for loggers but
    completes the compound-statement coverage.
    """
    tree = _parse(
        "class _Ctx:\n"
        "    def __enter__(self): return self\n"
        "    def __exit__(self, *a): return False\n"
        "with _Ctx():\n"
        "    logger = object()\n"
    )
    assert "logger" in _module_level_assign_targets(tree)


def test_module_level_assign_targets_descends_into_for_block() -> None:
    """A module-scope ``logger = ...`` nested under ``for`` must be caught.

    ``for`` at module scope does not create a new lexical scope; the loop
    variable and any assignments inside the body land in the module
    namespace. Rare for loggers but real for constant tables; pinning it
    here keeps the visitor symmetric with the import-containment twin.
    """
    tree = _parse("for _ in [0]:\n    logger = object()\n")
    assert "logger" in _module_level_assign_targets(tree)


def test_module_level_assign_targets_includes_import_as_binding() -> None:
    """``import structlog as logger`` at module scope must be caught.

    The original visitor only inspected ``Assign``/``AnnAssign`` nodes and
    therefore completely missed import-as bindings, which are the most
    natural way a future refactor might reintroduce a public ``logger``
    symbol (``import structlog as logger`` is shorter than
    ``logger = structlog.get_logger(__name__)``).
    """
    tree = _parse("import structlog as logger\n")
    assert "logger" in _module_level_assign_targets(tree)


def test_module_level_assign_targets_includes_from_import_as_binding() -> None:
    """``from structlog import get_logger as logger`` at module scope must be caught.

    Sister case to the ``import ... as`` binding — same bypass shape, same
    need to be covered. Omitted ``as`` on ``from x import y`` also binds
    ``y`` at module scope, so this validates the aliased form explicitly.
    """
    tree = _parse("from structlog import get_logger as logger\n")
    assert "logger" in _module_level_assign_targets(tree)


def test_module_level_assign_targets_includes_plain_from_import() -> None:
    """``from x import logger`` at module scope must be caught.

    Without an ``as`` clause, the imported name itself (``logger``) lands
    in the module namespace. The visitor must surface that as a bound
    name just like the aliased form.
    """
    tree = _parse("from some_pkg import logger\n")
    assert "logger" in _module_level_assign_targets(tree)


def test_module_level_assign_targets_skips_function_body() -> None:
    """A ``logger = ...`` inside a function body must NOT be flagged.

    ``def`` creates a new lexical scope; the binding is local to the
    function and invisible to ``from module import logger``. The visitor
    must skip function bodies explicitly, otherwise every helper with a
    local named ``logger`` would spuriously fail this gate.
    """
    tree = _parse("def helper():\n    logger = object()\n    return logger\n")
    assert "logger" not in _module_level_assign_targets(tree)


def test_module_level_assign_targets_skips_class_body() -> None:
    """A ``logger = ...`` inside a class body must NOT be flagged.

    Class bodies create their own namespace. A class-level ``logger``
    attribute is accessible as ``Class.logger`` but NOT as a module-scope
    import target. The visitor must skip class bodies for the same
    reason it skips function bodies.
    """
    tree = _parse("class Foo:\n    logger = object()\n")
    assert "logger" not in _module_level_assign_targets(tree)


def test_module_level_assign_targets_skips_async_function_body() -> None:
    """A ``logger = ...`` inside an ``async def`` body must NOT be flagged.

    ``async def`` is a separate AST node (``AsyncFunctionDef``) but creates
    a new lexical scope identically to ``FunctionDef``. Missing it would
    leave a bypass shape where ``async def f(): global logger; logger = ...``
    (or just a local inside an async helper) slipped through the gate.
    """
    tree = _parse("async def helper():\n    logger = object()\n    return logger\n")
    assert "logger" not in _module_level_assign_targets(tree)


def test_module_level_assign_targets_flattens_tuple_destructuring() -> None:
    """``a, logger = ...`` at module scope must flag ``logger`` (and ``a``).

    Tuple-unpacking at module scope is a legitimate binding form; the
    pre-fix helper already handled it, but the refactor must preserve the
    behaviour end-to-end. ``_names_in_target`` recurses into both
    ``ast.Tuple`` and ``ast.List`` destructuring targets via the tuple
    form ``isinstance(target, (ast.Tuple, ast.List))``.
    """
    tree = _parse("a, logger = 1, object()\n")
    names = _module_level_assign_targets(tree)
    assert "logger" in names
    assert "a" in names


def test_module_level_assign_targets_handles_ann_assign() -> None:
    """``logger: object = ...`` at module scope must flag ``logger``.

    ``AnnAssign`` is the typed form of assignment; the pre-fix helper
    already supported it, but the refactor must preserve that, too.
    """
    tree = _parse("logger: object = object()\n")
    assert "logger" in _module_level_assign_targets(tree)
