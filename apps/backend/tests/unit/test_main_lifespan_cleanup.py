"""Unit tests for the generic `_lifespan_cleanup` helper in `app.main`.

Issue #381: the old cleanup block hardcoded a 10-entry tuple of
`app.state` attribute names. Every time `deps.py` (or
`features/extraction/deps.py`) added a new lazily-cached dependency,
that tuple had to be kept in sync or the re-entered lifespan would
build a fresh attribute while the stale one leaked. The fix replaces
the tuple with a generic loop over the ``app.state`` attribute storage
that cleans every attribute not in ``_LIFESPAN_PRESERVED_ATTRS`` (the
small allowlist of process-scoped attrs ``create_app`` binds before
the lifespan runs).

The helper reads ``state._state`` directly when present — this is the
internal dict that starlette's ``State`` proxy class uses to hold all
user attributes. ``vars(state)`` on a starlette ``State`` only returns
``{"_state": dict}`` (the storage slot itself), not the user-visible
attributes, so iterating ``vars(state)`` and calling ``delattr`` in
that case would wipe starlette's internal storage dict rather than the
cached deps. The ``vars(state)`` fallback is kept for the
``SimpleNamespace`` test seam used below, where the user attributes
live directly in ``__dict__``.

These tests drive ``_lifespan_cleanup`` against synthetic ``app.state``
namespaces so the cleanup contract is verified without booting FastAPI,
Docling, or Ollama.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app import main as main_module
from app.main import _LIFESPAN_PRESERVED_ATTRS, _lifespan_cleanup


class _SpyLogger:
    """Test double for ``main_module._logger``.

    Why we don't use ``structlog.testing.capture_logs()`` here: ``app.main``
    defines ``_logger = structlog.get_logger(__name__)`` at import time,
    and our ``configure_logging()`` registers
    ``cache_logger_on_first_use=True``. Whichever test first touches the
    module's ``_logger`` outside a ``capture_logs()`` context can cause
    structlog to cache a bound logger that subsequent ``capture_logs()``
    contexts won't see — making log-assertion tests order-dependent. A
    direct monkeypatched spy sidesteps structlog's global state entirely,
    the same pattern used in
    ``tests/unit/features/extraction/test_extraction_service.py`` and
    ``tests/unit/features/extraction/test_router.py``.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **kwargs: object) -> None:  # pragma: no cover
        self.events.append((event, kwargs))

    def warning(self, event: str, **kwargs: object) -> None:
        self.events.append((event, kwargs))

    def error(self, event: str, **kwargs: object) -> None:  # pragma: no cover
        self.events.append((event, kwargs))


class _AcloseStub:
    """Minimal async-closeable stub that records how often `aclose` was awaited."""

    def __init__(self, *, raise_on_close: bool = False, label: str = "") -> None:
        self.aclose_calls = 0
        self._raise_on_close = raise_on_close
        self._label = label

    async def aclose(self) -> None:
        self.aclose_calls += 1
        if self._raise_on_close:
            msg = f"boom_{self._label}"
            raise RuntimeError(msg)


class _NoAcloseStub:
    """A plain value object without any close method.

    Represents attributes like ``span_resolver``, ``pdf_annotator``,
    ``text_concatenator`` — pipeline collaborators that are safe to keep
    alive across lifespan re-enters in principle, but whose cache entry
    must still be invalidated on shutdown so the next lifespan's
    ``Depends()`` graph resolves against a fresh set of collaborators.
    """


async def test_lifespan_cleanup_awaits_aclose_on_every_non_preserved_attr() -> None:
    """Every attr not in preserved_attrs must have ``aclose()`` awaited."""
    state = SimpleNamespace()
    probe = _AcloseStub(label="probe")
    provider = _AcloseStub(label="provider")
    state.probe = probe
    state.provider = provider

    await _lifespan_cleanup(state, preserved_attrs=frozenset())

    assert probe.aclose_calls == 1
    assert provider.aclose_calls == 1


async def test_lifespan_cleanup_delattrs_every_non_preserved_attr() -> None:
    """Cleanup must ``delattr`` each non-preserved attr so re-enter rebuilds fresh.

    This is the invariant the integration test ``test_lifespan_service_cache``
    pins at the transport layer. The unit test locks it at the helper boundary.
    """
    state = SimpleNamespace()
    state.cacheable_a = _NoAcloseStub()
    state.cacheable_b = _AcloseStub()

    await _lifespan_cleanup(state, preserved_attrs=frozenset())

    assert not hasattr(state, "cacheable_a")
    assert not hasattr(state, "cacheable_b")


async def test_lifespan_cleanup_preserves_exactly_the_allowlisted_attrs() -> None:
    """Only ``preserved_attrs`` must survive — no more, no less.

    ``create_app`` binds ``settings`` and ``skill_manifest`` to
    ``app.state`` BEFORE the lifespan runs. They are process-scoped and
    must outlive lifespan re-enters so the second lifespan reads the same
    configuration the first was built with. This test asserts set equality
    so an accidental extra preserved attr (or a silently dropped one)
    cannot slip past.
    """
    state = SimpleNamespace()
    state.settings = object()
    state.skill_manifest = object()

    # Simulate lifespan adding ephemeral attrs alongside preserved ones.
    state.probe = _AcloseStub()
    state.provider = _AcloseStub()

    await _lifespan_cleanup(state)

    remaining = set(vars(state).keys())
    assert remaining == {"settings", "skill_manifest"}


def test_default_preserved_attrs_contains_settings_and_skill_manifest() -> None:
    """Pin the default allowlist.

    ``create_app`` sets exactly these two attributes before the lifespan
    runs; a future refactor that removes one of them (or adds a third)
    must update the allowlist deliberately. Drift between the allowlist
    and ``create_app`` would leak the new long-lived dep on every
    lifespan shutdown.
    """
    assert "settings" in _LIFESPAN_PRESERVED_ATTRS
    assert "skill_manifest" in _LIFESPAN_PRESERVED_ATTRS


async def test_lifespan_cleanup_skips_aclose_on_non_closeable_attrs() -> None:
    """Attrs without ``aclose`` must still be delattr'd, with no spurious call attempt."""
    state = SimpleNamespace()
    state.plain_value = _NoAcloseStub()

    # Must not raise — the cleanup checks for `aclose` before calling it.
    await _lifespan_cleanup(state, preserved_attrs=frozenset())

    assert not hasattr(state, "plain_value")


async def test_lifespan_cleanup_logs_and_swallows_per_attr_aclose_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing ``aclose()`` must be logged via structlog and not propagate.

    Contract reasons:
    - Shutdown must close every other resource regardless of one failing.
    - Swallowing silently is forbidden by CLAUDE.md; the failure MUST be
      logged with ``lifespan_cleanup_failed`` event name so operators can
      diagnose leaked sockets after the fact.
    """
    spy = _SpyLogger()
    monkeypatch.setattr(main_module, "_logger", spy)

    state = SimpleNamespace()
    failing = _AcloseStub(raise_on_close=True, label="probe")
    succeeding = _AcloseStub(label="provider")
    state.failing = failing
    state.succeeding = succeeding

    await _lifespan_cleanup(state, preserved_attrs=frozenset())

    # Both were attempted; the failure did not short-circuit the loop.
    assert failing.aclose_calls == 1
    assert succeeding.aclose_calls == 1

    # Both attrs were removed — even the one that raised on aclose.
    assert not hasattr(state, "failing")
    assert not hasattr(state, "succeeding")

    # The failure was logged as a warning with the attribute name.
    matching = [
        (event, kwargs)
        for event, kwargs in spy.events
        if event == "lifespan_cleanup_failed" and kwargs.get("attr") == "failing"
    ]
    assert len(matching) == 1, (
        f"expected one lifespan_cleanup_failed log for 'failing', got: {spy.events}"
    )
    # The error class name is surfaced structurally (key=value) so operators
    # can grep for ``RuntimeError`` without parsing the event message.
    assert matching[0][1].get("error_class") == "RuntimeError"


async def test_lifespan_cleanup_logs_and_swallows_getattr_failure_on_aclose_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hostile ``__getattr__`` raising while probing for ``aclose`` must be logged and swallowed.

    The helper resolves ``aclose`` *inside* the try block so an attribute
    descriptor that raises during lookup (e.g. a property whose getter
    fails) is treated like any other cleanup error — logged under
    ``lifespan_cleanup_failed`` and not allowed to abort the cleanup loop.
    This guards against a future DI dep that wraps a resource in a lazy
    proxy whose ``aclose`` lookup itself requires network I/O.
    """
    spy = _SpyLogger()
    monkeypatch.setattr(main_module, "_logger", spy)

    class _HostileAttrLookup:
        """Raises on ANY attribute access, including the ``aclose`` probe."""

        def __getattr__(self, name: str) -> Any:
            msg = f"hostile_getattr_{name}"
            raise RuntimeError(msg)

    state = SimpleNamespace()
    state.hostile = _HostileAttrLookup()
    # Pair with a succeeding stub so we also verify cleanup keeps going.
    state.succeeding = _AcloseStub(label="ok")

    await _lifespan_cleanup(state, preserved_attrs=frozenset())

    # The hostile attribute was still removed (finally-block delattr).
    assert not hasattr(state, "hostile")
    # The sibling closeable was also processed; cleanup did not short-circuit.
    assert not hasattr(state, "succeeding")

    # The lookup failure was logged under the same event name so the
    # per-attr failure funnel is uniform.
    matching = [
        (event, kwargs)
        for event, kwargs in spy.events
        if event == "lifespan_cleanup_failed" and kwargs.get("attr") == "hostile"
    ]
    assert len(matching) == 1, (
        f"expected one lifespan_cleanup_failed log for 'hostile', got: {spy.events}"
    )
    assert matching[0][1].get("error_class") == "RuntimeError"


async def test_lifespan_cleanup_iterates_over_a_snapshot_not_live_state() -> None:
    """Iteration must snapshot-then-delete so the generator is not mid-mutated.

    Mutating ``vars(state)`` (via ``delattr``) while iterating over it raises
    ``RuntimeError: dictionary changed size during iteration``. The helper
    must materialize the to-clean list before mutating state.
    """
    state = SimpleNamespace()

    # Populate multiple attrs so the iteration actually exercises more than one step.
    for idx in range(5):
        setattr(state, f"dep_{idx}", _AcloseStub(label=f"dep_{idx}"))

    # Must not raise "dictionary changed size during iteration".
    await _lifespan_cleanup(state, preserved_attrs=frozenset())

    for idx in range(5):
        assert not hasattr(state, f"dep_{idx}")


async def test_lifespan_cleanup_is_noop_when_state_is_empty() -> None:
    """If the lifespan added nothing (e.g. early error path), cleanup must not crash."""
    state = SimpleNamespace()
    state.settings = object()

    await _lifespan_cleanup(state)

    # Pre-existing preserved attr remains, no crash.
    assert hasattr(state, "settings")


class _NonCallableAclose:
    """Attribute named ``aclose`` but not an async callable.

    Guards against false positives if a future DI dep happens to expose an
    ``aclose`` data attribute rather than a method. The cleanup must be
    robust enough to skip the attempt rather than propagate a ``TypeError``.
    """

    def __init__(self) -> None:
        self.aclose: Any = "not-a-coroutine"


async def test_lifespan_cleanup_tolerates_attribute_named_aclose_but_not_callable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-callable ``aclose`` attribute must be treated as "no aclose", not a TypeError."""
    spy = _SpyLogger()
    monkeypatch.setattr(main_module, "_logger", spy)

    state = SimpleNamespace()
    state.weird = _NonCallableAclose()

    # Must not crash.
    await _lifespan_cleanup(state, preserved_attrs=frozenset())

    # The attribute is cleared regardless.
    assert not hasattr(state, "weird")
    # If anything was logged, it must be the documented failure event —
    # never a bare exception propagation.
    assert all(event == "lifespan_cleanup_failed" for event, _ in spy.events)


async def test_lifespan_cleanup_handles_sync_aclose() -> None:
    """A sync ``aclose()`` (returns None rather than a coroutine) must also be called.

    The ``inspect.isawaitable`` check lets the helper accept both async and
    sync ``aclose`` methods. Some third-party clients (e.g. synchronous
    file-wrappers) might expose ``def aclose(self) -> None`` without the
    ``async`` keyword; the generic cleanup must still invoke them.
    """

    class _SyncCloseable:
        def __init__(self) -> None:
            self.closed = False

        def aclose(self) -> None:
            self.closed = True

    state = SimpleNamespace()
    closeable = _SyncCloseable()
    state.sync_dep = closeable

    await _lifespan_cleanup(state, preserved_attrs=frozenset())

    assert closeable.closed is True
    assert not hasattr(state, "sync_dep")
