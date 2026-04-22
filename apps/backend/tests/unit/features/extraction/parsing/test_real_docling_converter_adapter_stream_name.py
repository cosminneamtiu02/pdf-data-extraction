"""Unit tests for ``RealDoclingConverterAdapter.convert`` stream-name derivation (issue #383).

Docling's ``DocumentStream`` accepts a ``name`` kwarg which it may surface in
its logging/debug output. A hardcoded ``'input.pdf'`` makes concurrent-request
logs impossible to correlate. The adapter must derive the stream name from
the current structlog contextvars ``request_id`` when available, and fall back
to a fresh uuid hex otherwise. The ``.pdf`` suffix must be preserved so
Docling's filename-based format detection continues to route to the PDF
backend.

These tests monkeypatch the full ``docling.datamodel.base_models`` import
chain (plus the ``docling`` and ``docling.datamodel`` parent packages) with
stubs that capture the ``name`` kwarg, keeping the test offline and
honoring the third-party-containment contract (no real ``docling`` import
even if Docling is uninstalled).
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from typing import Any

import pytest
import structlog

from app.features.extraction.parsing._real_docling_converter_adapter import (
    RealDoclingConverterAdapter,
)


class _CapturingDocumentStream:
    """Stub that captures the ``name`` kwarg the adapter passes to Docling.

    Mirrors the shape of ``docling.datamodel.base_models.DocumentStream`` to
    the degree the adapter touches it: the constructor receives ``name`` and
    ``stream`` as keyword arguments and stores them for later inspection.
    """

    last_name: str | None = None

    def __init__(self, *, name: str, stream: Any) -> None:
        _CapturingDocumentStream.last_name = name
        self.name = name
        self.stream = stream


class _FakeDocumentResult:
    """Stub for the result object the real converter returns from ``convert``."""

    def __init__(self) -> None:
        self.document: Any = _FakeRawDoclingDocument()


class _FakeRawDoclingDocument:
    """Minimal shape satisfying ``RealDoclingDocumentAdapter``'s fallback path.

    ``iter_text_items`` is not exercised here — we only build the adapter so
    ``convert`` can complete. The adapter wrapper stores the doc and only
    reads it on demand.
    """

    texts: list[Any] = []  # noqa: RUF012 - class-level empty sentinel for stub
    pages: dict[int, Any] = {}  # noqa: RUF012 - class-level empty sentinel for stub


class _FakeRealConverter:
    """Fake converter recording the ``source`` it was handed by ``convert``."""

    def __init__(self) -> None:
        self.last_source: Any = None

    def convert(self, source: Any) -> _FakeDocumentResult:
        self.last_source = source
        return _FakeDocumentResult()


def _install_fake_base_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a stub ``docling.datamodel.base_models`` exposing the capturing stream.

    Stubs the full import chain — ``docling``, ``docling.datamodel``, and
    ``docling.datamodel.base_models`` — so ``importlib.import_module(
    "docling.datamodel.base_models")`` resolves without touching the real
    Docling package. Setting ``__path__ = []`` on the parent modules marks
    them as packages, which is what CPython's import machinery walks on a
    cold cache.
    """
    docling_mod = type(sys)("docling")
    docling_mod.__path__ = []  # type: ignore[attr-defined]

    datamodel_mod = type(sys)("docling.datamodel")
    datamodel_mod.__path__ = []  # type: ignore[attr-defined]

    base_models_mod = type(sys)("docling.datamodel.base_models")
    base_models_mod.DocumentStream = _CapturingDocumentStream  # type: ignore[attr-defined]

    docling_mod.datamodel = datamodel_mod  # type: ignore[attr-defined]
    datamodel_mod.base_models = base_models_mod  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "docling", docling_mod)
    monkeypatch.setitem(sys.modules, "docling.datamodel", datamodel_mod)
    monkeypatch.setitem(sys.modules, "docling.datamodel.base_models", base_models_mod)


def _reset_capture() -> None:
    _CapturingDocumentStream.last_name = None


@pytest.fixture(autouse=True)
def _clear_structlog_contextvars() -> Iterator[None]:
    """Clear ``structlog.contextvars`` around every test in this module.

    ``structlog.contextvars`` is process-global state. Tests that ``bind``
    a ``request_id`` must not leak into sibling tests even if an
    intermediate assertion raises — so we unconditionally clear on setup
    and teardown instead of relying on each test's own ``try/finally``.
    """
    structlog.contextvars.clear_contextvars()
    try:
        yield
    finally:
        structlog.contextvars.clear_contextvars()


def test_convert_uses_request_id_as_stream_name_when_bound_in_contextvars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bound ``request_id`` must become ``<request_id>.pdf`` on the stream."""
    _install_fake_base_models(monkeypatch)
    _reset_capture()
    structlog.contextvars.bind_contextvars(request_id="req-abc123")

    adapter = RealDoclingConverterAdapter(_FakeRealConverter())
    adapter.convert(b"%PDF-fake")

    assert _CapturingDocumentStream.last_name == "req-abc123.pdf"


def test_convert_falls_back_to_uuid_hex_when_request_id_not_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no ``request_id`` in contextvars, the name must be ``<uuid4-hex>.pdf``."""
    _install_fake_base_models(monkeypatch)
    _reset_capture()

    adapter = RealDoclingConverterAdapter(_FakeRealConverter())
    adapter.convert(b"%PDF-fake")

    captured = _CapturingDocumentStream.last_name
    assert captured is not None
    assert re.fullmatch(r"[0-9a-f]{32}\.pdf", captured) is not None, (
        f"expected uuid-hex fallback name, got {captured!r}"
    )


def test_convert_falls_back_to_uuid_hex_when_request_id_is_non_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-string ``request_id`` (e.g., an int leaked from some binder) must
    not propagate into the Docling stream name. The adapter must treat it as
    absent and fall back to a uuid hex so the invariant "the name is a
    well-formed ``<hex>.pdf`` or ``<token>.pdf``" holds for any bound value.
    """
    _install_fake_base_models(monkeypatch)
    _reset_capture()
    structlog.contextvars.bind_contextvars(request_id=12345)  # non-string

    adapter = RealDoclingConverterAdapter(_FakeRealConverter())
    adapter.convert(b"%PDF-fake")

    captured = _CapturingDocumentStream.last_name
    assert captured is not None
    assert re.fullmatch(r"[0-9a-f]{32}\.pdf", captured) is not None, (
        f"expected uuid-hex fallback when request_id is non-string, got {captured!r}"
    )


def test_convert_produces_distinct_fallback_names_across_invocations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Back-to-back fallback paths must not reuse the same uuid (correlation-id
    reuse would defeat the point of the fix).

    Deterministic: monkeypatches the adapter's ``uuid.uuid4`` with a
    generator that yields two distinct, known hex values, asserts the
    captured names match exactly, and that the fake generator was drained
    (i.e., the adapter actually called ``uuid4`` twice).
    """
    _install_fake_base_models(monkeypatch)

    # Two fixed 32-hex sentinels stand in for ``uuid4()`` return values.
    # The adapter calls ``.hex`` on the result, so the fake must expose
    # a ``.hex`` attribute — a simple namespace object is enough.
    class _FakeUuid:
        def __init__(self, hex_value: str) -> None:
            self.hex = hex_value

    fake_hexes = iter(["a" * 32, "b" * 32])

    def _fake_uuid4() -> _FakeUuid:
        return _FakeUuid(next(fake_hexes))

    # Replace the adapter module's bound ``uuid`` reference with a stub
    # exposing only ``uuid4``. This isolates the patch to the module
    # under test and leaves the real ``uuid`` module untouched for any
    # parallel imports.
    import app.features.extraction.parsing._real_docling_converter_adapter as adapter_mod

    fake_uuid_module = type(sys)("uuid_stub")
    fake_uuid_module.uuid4 = _fake_uuid4  # type: ignore[attr-defined]
    monkeypatch.setattr(adapter_mod, "uuid", fake_uuid_module)

    adapter = RealDoclingConverterAdapter(_FakeRealConverter())

    _reset_capture()
    adapter.convert(b"%PDF-fake")
    first_name = _CapturingDocumentStream.last_name

    _reset_capture()
    adapter.convert(b"%PDF-fake")
    second_name = _CapturingDocumentStream.last_name

    assert first_name == "a" * 32 + ".pdf"
    assert second_name == "b" * 32 + ".pdf"
    # Generator drained — a third call would raise StopIteration,
    # confirming the adapter called ``uuid4`` exactly twice (once per
    # ``convert``) and did not reuse a cached token.
    with pytest.raises(StopIteration):
        next(fake_hexes)
