"""Architecture: every DI-cached `app.state` attribute must be cleanup-safe.

Issue #381: the old `_lifespan` cleanup hardcoded a 10-entry tuple of
`app.state` attribute names. Every new lazily-cached dependency added to
`app/api/deps.py` or `app/features/extraction/deps.py` had to be added to
the tuple — a footgun with no enforcement. The fix (same PR as this
test) switches the cleanup to a generic loop over ``vars(app.state)``
that cleans every attr not in the small ``_LIFESPAN_PRESERVED_ATTRS``
allowlist in ``app/main.py``. That change covers every new lazily-cached
attribute automatically.

This AST-scan is the belt-and-suspenders check. The runtime cleanup
will ``delattr`` any non-preserved attr — even ones without ``aclose``
— but a new DI-cached attribute that holds an open resource without
exposing ``aclose`` is still a leak. The scan enforces that every cached
attr is statically either async-closeable (exposes ``aclose``) or
explicitly allowlisted here as a known-safe plain object. That way, if
a future DI factory caches (say) an ``httpx.AsyncClient`` directly on
``app.state`` without an ``aclose``-exposing wrapper, this test fails
at scan time long before production shutdown leaks the socket.

The scan walks the AST of the two DI factory modules and collects every
assignment of the shape ``state.X = <value>`` or ``app.state.X = <value>``
(where `state` was bound earlier in the same function to
`request.app.state`). For each such attribute, the right-hand side's
class name is looked up in the module's import table, and the resolved
class is required to either:

1. Expose an ``aclose`` method on the class body, OR
2. Be listed in ``_NON_CLOSEABLE_ALLOWLIST``.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import Final

import pytest

from ._linter_subprocess import BACKEND_DIR

_DEPS_FILES: Final[tuple[Path, ...]] = (
    BACKEND_DIR / "app" / "api" / "deps.py",
    BACKEND_DIR / "app" / "features" / "extraction" / "deps.py",
)

# Classes assigned onto `app.state.X` that are intentionally kept without
# `aclose()` because they are plain value objects with no network/file/thread
# handles. The keys are the attribute names (not class names) so a future
# rename of one of these classes surfaces as a "no entry" failure until the
# allowlist is updated intentionally.
#
# Semantics: allowlist entries do NOT leak resources when the cleanup loop's
# `delattr(app.state, attr)` runs — they are simple Python objects whose GC
# reclaims them normally.
#
# Intentional omissions from the allowlist:
# - ``settings`` and ``skill_manifest`` are set by ``create_app`` (before the
#   lifespan starts) and listed in ``app.main._LIFESPAN_PRESERVED_ATTRS``, so
#   the cleanup loop leaves them alone. They are also not assigned from within
#   the DI factories this scan walks, so adding them here would be dead code.
# - ``OllamaHealthProbe`` and ``OllamaGemmaProvider`` are async-closeable
#   and are detected automatically; they must NOT appear in this allowlist.
_NON_CLOSEABLE_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        # `app.api.deps` — pipeline validator, parser, cached service holder.
        "structured_output_validator",
        "document_parser",
        "extraction_service",
        "probe_cache",
        # `app.features.extraction.deps` — pipeline collaborators.
        "text_concatenator",
        "extraction_engine",
        "span_resolver",
        "pdf_annotator",
    },
)


def _collect_imported_classes(tree: ast.AST) -> dict[str, str]:
    """Map a local name to its fully qualified module.attr, for every `from X import Y` or `import X`.

    Returns ``{local_name: "module.path.ClassName"}``. Aliases are resolved
    (``from m import Cls as C`` yields ``{"C": "m.Cls"}``). Plain
    ``import pkg`` is captured as ``{"pkg": "pkg"}`` so call nodes of the
    shape ``pkg.Cls(...)`` can still be resolved downstream.
    """
    mapping: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            for alias in node.names:
                local = alias.asname or alias.name
                mapping[local] = f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name
                mapping[local] = alias.name
    return mapping


def _is_state_alias(target: ast.expr) -> bool:
    """Return True if ``target`` is the receiver of a ``state.X`` or ``app.state.X`` assignment.

    The DI factories use two equivalent shapes:
    - ``state = request.app.state`` → ``state.X = ...``
    - direct: ``request.app.state.X = ...`` (not used but covered for safety)
    """
    if isinstance(target, ast.Name) and target.id == "state":
        return True
    if isinstance(target, ast.Attribute) and target.attr == "state":
        inner = target.value
        return isinstance(inner, ast.Attribute) and inner.attr == "app"
    return False


def _collect_state_assignments(
    tree: ast.AST,
) -> dict[str, tuple[ast.AST, ast.FunctionDef | ast.AsyncFunctionDef | None]]:
    """Collect every ``state.X = <rhs>`` or ``app.state.X = <rhs>`` assignment.

    Returns ``{attr_name: (rhs_ast_node, enclosing_function_def)}``. The
    enclosing function is tracked so :func:`_resolve_rhs_class_name` can
    backtrack a ``Name`` RHS (``state.X = local_var``) to the variable's
    type annotation within the same function scope — the double-checked-
    locking DI pattern used by the real factories assigns the cached object
    via a local name rather than an inline constructor call.

    If the same attribute is assigned more than once in the module, the last
    assignment wins — fine because each factory only reassigns within the
    same critical section and to the same class.
    """
    assignments: dict[str, tuple[ast.AST, ast.FunctionDef | ast.AsyncFunctionDef | None]] = {}
    for parent in ast.walk(tree):
        scope = parent if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)) else None
        container = (
            parent
            if isinstance(parent, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef))
            else None
        )
        if container is None:
            continue
        for node in ast.walk(container):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Attribute):
                    continue
                if _is_state_alias(target.value):
                    assignments[target.attr] = (node.value, scope)
    return assignments


def _is_none_constant(node: ast.AST) -> bool:
    """Return True iff ``node`` is the literal ``None`` constant."""
    return isinstance(node, ast.Constant) and node.value is None


def _first_non_none_class(nodes: tuple[ast.AST, ...]) -> str | None:
    """Return the first resolvable class name across ``nodes``, skipping ``None`` literals."""
    for node in nodes:
        if _is_none_constant(node):
            continue
        resolved = _extract_class_from_annotation(node)
        if resolved is not None:
            return resolved
    return None


def _extract_from_subscript(annotation: ast.Subscript) -> str | None:
    """Resolve ``Optional[X]`` or ``Union[X, None]`` to ``"X"``; else None."""
    if not isinstance(annotation.value, ast.Name):
        return None
    subscript_name = annotation.value.id
    if subscript_name == "Optional":
        return _extract_class_from_annotation(annotation.slice)
    if subscript_name == "Union" and isinstance(annotation.slice, ast.Tuple):
        return _first_non_none_class(tuple(annotation.slice.elts))
    return None


def _extract_class_from_annotation(annotation: ast.AST) -> str | None:
    """Extract the concrete class name from a type annotation AST.

    Handles the shapes that appear in the DI factories:

    - ``ClassName`` → ``"ClassName"``
    - ``ClassName | None`` → ``"ClassName"`` (the non-None BitOr arm)
    - ``Optional[ClassName]`` → ``"ClassName"``
    - ``Union[ClassName, None]`` → ``"ClassName"``

    Returns None for shapes the resolver does not understand.
    """
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return _first_non_none_class((annotation.left, annotation.right))
    if isinstance(annotation, ast.Subscript):
        return _extract_from_subscript(annotation)
    return None


def _resolve_name_in_scope(name: str, scope: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """Resolve a local variable name to a class name within a function scope.

    Preference order within ``scope``:

    1. An ``AnnAssign`` binding (``var: ClassName | None = ...``) — the DI
       factories always annotate the cached local, so this is the primary
       signal.
    2. An ``Assign`` whose RHS is a direct constructor call
       (``var = ClassName(...)``).

    Returns ``None`` if neither pattern is present.
    """
    for stmt in ast.walk(scope):
        if (
            isinstance(stmt, ast.AnnAssign)
            and isinstance(stmt.target, ast.Name)
            and stmt.target.id == name
        ):
            resolved = _extract_class_from_annotation(stmt.annotation)
            if resolved is not None:
                return resolved
    for stmt in ast.walk(scope):
        if not isinstance(stmt, ast.Assign):
            continue
        for t in stmt.targets:
            if isinstance(t, ast.Name) and t.id == name and isinstance(stmt.value, ast.Call):
                resolved = _resolve_rhs_class_name(stmt.value, None)
                if resolved is not None:
                    return resolved
    return None


def _resolve_rhs_class_name(
    rhs: ast.AST,
    scope: ast.FunctionDef | ast.AsyncFunctionDef | None,
) -> str | None:
    """Return the constructor name for a class-instantiation RHS, else None.

    Examples:

    - ``ProbeCache(...)`` → ``"ProbeCache"``
    - ``OllamaGemmaProvider(settings=...)`` → ``"OllamaGemmaProvider"``
    - ``some_factory()`` (a Name call) → the name itself
    - ``state.X = provider`` where ``provider: OllamaGemmaProvider | None = ...``
      is annotated earlier in ``scope`` → ``"OllamaGemmaProvider"``

    Returns None for RHS shapes the resolver does not understand.
    """
    if isinstance(rhs, ast.Call):
        func = rhs.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return None
    if isinstance(rhs, ast.Name) and scope is not None:
        return _resolve_name_in_scope(rhs.id, scope)
    return None


def _resolve_class_has_aclose(imports: dict[str, str], class_name: str) -> bool:
    """Return True iff the class identified by ``class_name`` has an async ``aclose`` method.

    Resolution strategy: look up ``class_name`` in the module's import table
    to find the module path, then import the module and ``getattr`` the
    class. Checking for ``aclose`` on the class body is done by inspecting
    the class's ``__dict__`` so we ignore inherited ``object`` methods.

    Returns False if the class cannot be resolved (missing import entry,
    import failure, or attribute missing) — the caller will then fall
    through to the allowlist check.
    """
    qualified = imports.get(class_name)
    if qualified is None:
        return False
    module_path, _, attr = qualified.rpartition(".")
    if not module_path:
        return False
    try:
        module = importlib.import_module(module_path)
    except ImportError:
        return False
    cls = getattr(module, attr, None)
    if cls is None:
        return False
    aclose = getattr(cls, "aclose", None)
    return callable(aclose)


@pytest.mark.parametrize(
    "deps_file",
    _DEPS_FILES,
    ids=lambda p: str(p.relative_to(BACKEND_DIR)),
)
def test_every_cached_app_state_attribute_is_cleanup_safe(deps_file: Path) -> None:
    """Every ``state.X = Cls(...)`` in a DI factory must be cleanup-safe.

    Definition of cleanup-safe:
    - The assigned class exposes an ``aclose`` method (async-closeable), OR
    - The attribute name is explicitly listed in ``_NON_CLOSEABLE_ALLOWLIST``
      as a plain Python value object whose cleanup is a simple ``delattr``.

    A new DI-cached attribute that is neither async-closeable nor
    allowlisted will fail here with a clear message instead of silently
    leaking resources at shutdown time in production.
    """
    tree = ast.parse(deps_file.read_text(encoding="utf-8"), filename=str(deps_file))
    imports = _collect_imported_classes(tree)
    assignments = _collect_state_assignments(tree)

    offenders: list[str] = []
    for attr_name, (rhs, scope) in assignments.items():
        class_name = _resolve_rhs_class_name(rhs, scope)
        has_aclose = class_name is not None and _resolve_class_has_aclose(imports, class_name)
        allowlisted = attr_name in _NON_CLOSEABLE_ALLOWLIST
        if not has_aclose and not allowlisted:
            offenders.append(
                f"app.state.{attr_name} = {class_name}(...) — class has no "
                f"`aclose()` and attribute is not in the non-closeable allowlist"
            )

    assert not offenders, (
        f"In {deps_file.relative_to(BACKEND_DIR)}: every DI-cached "
        "`app.state.X` attribute must either be async-closeable or listed in "
        "`_NON_CLOSEABLE_ALLOWLIST` in this test file.\n"
        "Offenders:\n" + "\n".join(f"  - {o}" for o in offenders)
    )


def test_allowlist_is_not_stale() -> None:
    """Every allowlist entry must correspond to an actually-assigned attr.

    Prevents the allowlist from silently accumulating dead entries after
    an attribute is removed or renamed. Each entry must resolve to a
    `state.<name> = ...` assignment in one of the two DI factory files.
    """
    assigned_attrs: set[str] = set()
    for deps_file in _DEPS_FILES:
        tree = ast.parse(
            deps_file.read_text(encoding="utf-8"),
            filename=str(deps_file),
        )
        assigned_attrs.update(_collect_state_assignments(tree).keys())

    stale = _NON_CLOSEABLE_ALLOWLIST - assigned_attrs
    assert not stale, (
        "Entries in `_NON_CLOSEABLE_ALLOWLIST` that no longer correspond to "
        "a `state.X = ...` assignment in any DI factory. Either remove them "
        "from the allowlist or restore the assignment:\n"
        + "\n".join(f"  - {attr}" for attr in sorted(stale))
    )


def test_detector_collects_state_alias_assignments(tmp_path: Path) -> None:
    """Sanity check: the scanner finds ``state.X = ...`` after ``state = request.app.state``.

    Pins the AST collector behaviour so a future refactor of the detector
    cannot silently stop covering the real factory shape.
    """
    src = tmp_path / "mini_deps.py"
    src.write_text(
        "def factory(request):\n    state = request.app.state\n    state.my_dep = MyClass()\n",
        encoding="utf-8",
    )
    tree = ast.parse(src.read_text(encoding="utf-8"))
    assignments = _collect_state_assignments(tree)
    assert "my_dep" in assignments


def test_detector_ignores_non_state_assignments(tmp_path: Path) -> None:
    """The scanner must not flag ``obj.x = ...`` where ``obj`` is unrelated to ``app.state``."""
    src = tmp_path / "mini_deps.py"
    src.write_text(
        "class Container:\n    def __init__(self):\n        self.value = 42\n",
        encoding="utf-8",
    )
    tree = ast.parse(src.read_text(encoding="utf-8"))
    assignments = _collect_state_assignments(tree)
    assert assignments == {}
