"""Unit tests for the generic `_lifespan_cleanup` helper in `app.main`.

Issue #381: the old cleanup block hardcoded a 10-entry tuple of
`app.state` attribute names. Every time `deps.py` (or
`features/extraction/deps.py`) added a new lazily-cached dependency,
that tuple had to be kept in sync or the re-entered lifespan would
build a fresh attribute while the stale one leaked. The fix replaces
the tuple with a generic loop over ``vars(app.state).items()`` that
cleans every attribute not in ``_LIFESPAN_PRESERVED_ATTRS`` (the small
allowlist of process-scoped attrs ``create_app`` binds before the
lifespan runs).

These tests drive ``_lifespan_cleanup`` against synthetic ``app.state``
namespaces so the cleanup contract is verified without booting FastAPI,
Docling, or Ollama.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from structlog.testing import capture_logs

from app.main import _LIFESPAN_PRESERVED_ATTRS, _lifespan_cleanup


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


async def test_lifespan_cleanup_preserves_allowlisted_attrs() -> None:
    """Attrs in ``preserved_attrs`` (e.g. ``settings``, ``skill_manifest``) must survive.

    ``create_app`` binds ``settings`` and ``skill_manifest`` to
    ``app.state`` BEFORE the lifespan runs. They are process-scoped and
    must outlive lifespan re-enters so the second lifespan reads the same
    configuration the first was built with.
    """
    state = SimpleNamespace()
    state.settings = object()
    state.skill_manifest = object()

    # Simulate lifespan adding ephemeral attrs alongside preserved ones.
    state.probe = _AcloseStub()

    await _lifespan_cleanup(state)

    assert hasattr(state, "settings")
    assert hasattr(state, "skill_manifest")
    assert not hasattr(state, "probe")


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


async def test_lifespan_cleanup_logs_and_swallows_per_attr_aclose_failures() -> None:
    """A failing ``aclose()`` must be logged via structlog and not propagate.

    Contract reasons:
    - Shutdown must close every other resource regardless of one failing.
    - Swallowing silently is forbidden by CLAUDE.md; the failure MUST be
      logged with ``lifespan_cleanup_failed`` event name so operators can
      diagnose leaked sockets after the fact.
    """
    state = SimpleNamespace()
    failing = _AcloseStub(raise_on_close=True, label="probe")
    succeeding = _AcloseStub(label="provider")
    state.failing = failing
    state.succeeding = succeeding

    with capture_logs() as logs:
        await _lifespan_cleanup(state, preserved_attrs=frozenset())

    # Both were attempted; the failure did not short-circuit the loop.
    assert failing.aclose_calls == 1
    assert succeeding.aclose_calls == 1

    # Both attrs were removed — even the one that raised on aclose.
    assert not hasattr(state, "failing")
    assert not hasattr(state, "succeeding")

    # The failure was logged as a warning with the attribute name.
    matching = [
        entry
        for entry in logs
        if entry.get("event") == "lifespan_cleanup_failed" and entry.get("attr") == "failing"
    ]
    assert len(matching) == 1, (
        f"expected one lifespan_cleanup_failed log for 'failing', got: {logs}"
    )
    assert matching[0].get("log_level") == "warning"
    # The error class name is surfaced structurally (key=value) so operators
    # can grep for ``RuntimeError`` without parsing the event message.
    assert matching[0].get("error_class") == "RuntimeError"


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


async def test_lifespan_cleanup_tolerates_attribute_named_aclose_but_not_callable() -> None:
    """A non-callable ``aclose`` attribute must be treated as "no aclose", not a TypeError."""
    state = SimpleNamespace()
    state.weird = _NonCallableAclose()

    with capture_logs() as logs:
        # Must not crash.
        await _lifespan_cleanup(state, preserved_attrs=frozenset())

    # The attribute is cleared regardless.
    assert not hasattr(state, "weird")
    # If anything was logged, it must be the documented failure event —
    # never a bare exception propagation.
    assert all(entry.get("event") in (None, "lifespan_cleanup_failed") for entry in logs)


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
