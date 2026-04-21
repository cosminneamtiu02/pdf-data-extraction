"""Adapter from Docling's concrete ``DoclingDocument`` to the parser's Protocol.

This file, together with ``_real_docling_converter_adapter.py`` and
``docling_document_parser.py``, is part of the Docling containment
boundary (import-linter contract C3). These three files — and nothing
else in the service — are allowed to reference Docling's types. They
present the local ``DoclingDocumentLike`` / ``DoclingConverterLike``
Protocols to the rest of the parser so downstream code never learns
anything about Docling's class hierarchy.

The adapter itself does not statically import ``docling`` — it operates on
``Any``-typed objects passed in by the converter-adapter factory — but it
lives inside the C3 boundary because any reasonable shape-change from
Docling would ripple here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

import structlog

from app.exceptions import PdfParserUnavailableError
from app.features.extraction.parsing._flat_docling_text_item import FlatDoclingTextItem

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.features.extraction.parsing._docling_text_item_like import DoclingTextItemLike

_log = structlog.get_logger(__name__)


class RealDoclingDocumentAdapter:
    """Adapts Docling's ``DoclingDocument`` to ``DoclingDocumentLike``.

    Implements ``iter_text_items`` by walking Docling's text-bearing node items
    and translating each provenance entry into a simple flat item that
    exposes bottom-left-origin coordinates. Docling's ``BoundingBox`` carries a
    ``coord_origin`` field (``CoordOrigin.TOPLEFT`` or ``CoordOrigin.BOTTOMLEFT``) —
    TOPLEFT is in fact Docling's *default* for most pipeline outputs — so the
    adapter cannot assume one origin. When a prov bbox reports TOPLEFT
    coordinates (via ``coord_origin``), the adapter flips it to BOTTOMLEFT with
    ``bbox.to_bottom_left_origin(page_height=...)`` before unpacking, using the
    owning page's height from ``doc.pages[page_no].size``; boxes already in
    BOTTOMLEFT are unpacked as-is. This matches our canonical ``BoundingBox``
    convention (origin bottom-left, ``y0 <= y1``) and matches PyMuPDF, which
    the annotator uses downstream without further transformation. (GH issue
    #133.)

    Reading order (GH issue #150): the traversal delegates to Docling's
    public ``DoclingDocument.iterate_items()`` API, which walks the document
    hierarchy in visual READING order (top-to-bottom, left-to-right per
    page, respecting column and table structure). Iterating ``.texts``
    directly — as an earlier revision did — returns items in Docling's
    implementation-dependent STORAGE order, which on multi-column / table-
    heavy layouts interleaves columns incorrectly and causes downstream
    span-resolution failures (LangExtract reports ``hallucinated_offsets``
    for values that are in fact present in the source PDF). The fallback
    to ``.texts`` is retained only for documents whose shape predates
    ``iterate_items()`` (e.g., minimal test doubles in other features' fakes);
    real Docling-produced documents always expose ``iterate_items()``.
    """

    def __init__(self, docling_document: Any) -> None:
        self._docling_document: Any = docling_document

    @property
    def page_count(self) -> int:
        pages: Any = self._docling_document.pages
        return len(pages)

    def iter_text_items(self) -> Iterable[DoclingTextItemLike]:
        pages: Any = getattr(self._docling_document, "pages", None) or {}
        for text_item in self._iter_reading_order():
            item: Any = text_item
            text_value: Any = getattr(item, "text", None)
            provs: Any = getattr(item, "prov", None) or []
            if not text_value or not provs:
                continue
            prov: Any = provs[0]
            page_no: int = int(prov.page_no)
            raw_bbox: Any = prov.bbox
            # Docling's ``CoordOrigin`` is a ``str, Enum`` whose members stringify
            # to ``CoordOrigin.TOPLEFT`` / ``CoordOrigin.BOTTOMLEFT`` (standard
            # ``Enum.__str__``). Match on ``str(origin).endswith("TOPLEFT")`` so
            # the check accepts both the real enum's string form and plain-
            # string test doubles like ``"TOPLEFT"``.
            origin: Any = getattr(raw_bbox, "coord_origin", None)
            needs_flip: bool = origin is not None and str(origin).endswith("TOPLEFT")
            if needs_flip:
                # Direct indexing: if ``page_no`` is missing from ``doc.pages``,
                # raise KeyError with the offending page number instead of
                # silently returning None and blowing up later on ``.size``.
                page: Any = pages[page_no]
                page_height: float = float(page.size.height)
                bbox: Any = raw_bbox.to_bottom_left_origin(page_height=page_height)
            else:
                bbox = raw_bbox
            yield FlatDoclingTextItem(
                text=str(text_value),
                page_number=page_no,
                bbox_x0=float(bbox.l),
                bbox_y0=float(bbox.b),
                bbox_x1=float(bbox.r),
                bbox_y1=float(bbox.t),
            )

    def _iter_reading_order(self) -> Iterable[Any]:
        """Yield every Docling node item in visual reading order.

        Uses ``DoclingDocument.iterate_items()`` when available — this is
        Docling's public reading-order traversal API (Docling >= 2.x) and
        yields ``(item, level)`` tuples for every node in the document tree,
        in top-to-bottom / left-to-right / per-column visual order. The
        caller filters down to text-bearing items by testing ``.text`` and
        ``.prov`` attributes, so non-text nodes (tables, pictures) are
        ignored without a type check against Docling's own class hierarchy
        — preserving this file's containment of Docling's public types.

        When ``iterate_items()`` is not available (legacy documents or test
        doubles that predate this API), the adapter falls back to iterating
        ``.texts`` directly, which is Docling's internal storage order. That
        fallback is imperfect on multi-column layouts but keeps the adapter
        robust against shape drift. Real Docling-produced documents always
        expose ``iterate_items()``.

        If neither a callable ``iterate_items()`` nor a proper iterable
        ``.texts`` sequence is available — a Docling shape change that the
        adapter does not understand — the method raises
        ``PdfParserUnavailableError`` with ``dependency='docling'`` so
        operators see the drift at the true failure site. An earlier
        revision returned ``[]`` in that case
        (``getattr(..., "texts", None) or []``), which silently truncated
        the reading-order stream and let the parser misattribute the
        failure to the PDF (surfacing as ``PDF_NO_TEXT_EXTRACTABLE``).
        ``str`` / ``bytes`` / ``bytearray`` / ``Mapping`` values of
        ``.texts`` are rejected explicitly because iterating them yields
        characters / byte-ints / dict keys, each of which has no ``.text``
        / ``.prov`` and so re-introduces the same silent-truncation
        failure mode. The emitted ``docling_shape_unrecognized`` log
        distinguishes ``has_iterate_items_attr`` (attribute present at
        all) from ``iterate_items_callable`` (present AND callable) so
        operators can tell a rename-to-property shape drift from a
        removed-entirely shape drift. Issue #341.
        """
        sentinel = object()
        iterate_items: Any = getattr(self._docling_document, "iterate_items", sentinel)
        has_iterate_items_attr: bool = iterate_items is not sentinel
        iterate_items_callable: bool = callable(iterate_items)
        if iterate_items_callable:
            iterator: Any = iterate_items()
            for pair in iterator:
                item: Any = pair[0]
                yield item
            return
        # Fallback path: `.texts` must be present AND a *proper* iterable of
        # node items. An empty iterable is fine (legitimately empty
        # document); a missing attribute, a scalar value, a `str` / `bytes`
        # / `bytearray`, or a `Mapping` all signal a Docling shape change.
        # `str`-like and `Mapping` types are technically iterable but
        # iterating them yields characters / byte-ints / dict keys, every
        # one of which has no `.text` / `.prov` so the adapter would
        # silently yield zero items — exactly the failure mode this guard
        # exists to prevent (Issue #341, Copilot follow-up). Validate with
        # an explicit `iter(texts)` attempt so the check rejects non-
        # iterables uniformly even if they happen to expose a stray
        # `__iter__` attribute.
        texts: Any = getattr(self._docling_document, "texts", sentinel)
        is_rejected_texts_type: bool = isinstance(texts, str | bytes | bytearray | Mapping)
        iter_failed: bool = False
        if texts is not sentinel and not is_rejected_texts_type:
            try:
                iter(texts)
            except TypeError:
                iter_failed = True
        if texts is sentinel or is_rejected_texts_type or iter_failed:
            document_type: str = type(self._docling_document).__name__
            # ``texts`` is narrowed to
            # ``Any | str | bytes | bytearray | Mapping[Unknown, Unknown]``
            # after the ``isinstance`` guard above, so cast back to ``Any``
            # before asking Pyright to resolve ``type(...).__name__``; we
            # only want the runtime class name, not a parameterised
            # generic type.
            texts_type: str = (
                "<missing>" if texts is sentinel else type(cast("Any", texts)).__name__
            )
            _log.error(
                "docling_shape_unrecognized",
                document_type=document_type,
                has_iterate_items_attr=has_iterate_items_attr,
                iterate_items_callable=iterate_items_callable,
                has_texts=texts is not sentinel,
                texts_type=texts_type,
                detail=(
                    "DoclingDocument exposes neither a callable `iterate_items` "
                    "nor a proper iterable `.texts` attribute (str/bytes/Mapping "
                    "are rejected because iterating them yields non-node values). "
                    "This indicates Docling shape drift; the adapter cannot "
                    "surface any text items."
                ),
            )
            raise PdfParserUnavailableError(dependency="docling")
        yield from texts


__all__ = ["RealDoclingDocumentAdapter"]
