"""PdfAnnotator: render source-grounded highlights onto the input PDF via PyMuPDF.

This file is the only place in the extraction feature permitted to import
PyMuPDF (`pymupdf` / legacy alias `fitz`). Containment is enforced
mechanically by the AST-scan unit test in the sibling test module, and from
PDFX-E007-F004 onward by import-linter. All other layers depend on
`ExtractedField` / `BoundingBoxRef` schemas, never on PyMuPDF types.

Coordinate system note: `BoundingBoxRef` uses **bottom-left** PDF-native
coordinates, but PyMuPDF's drawing APIs (including `Page.add_highlight_annot`)
take rects in its internal **top-left** MuPDF coordinate system, where y grows
downward from the top edge of the page. A bbox passed straight through would
render flipped vertically (e.g., a region 10 px from the bottom would appear
10 px from the top). `_draw_highlight` therefore converts each bbox using
`y_mupdf = page_height - y_pdf` before constructing the `pymupdf.Rect`.
"""

import asyncio
from typing import Any, cast

import pymupdf

from app.features.extraction.schemas.bounding_box_ref import BoundingBoxRef
from app.features.extraction.schemas.extracted_field import ExtractedField


class PdfAnnotator:
    """Draw highlight annotations at each `BoundingBoxRef` on the input PDF.

    Fields with empty ``bbox_refs`` are skipped silently — no annotation, no
    warning, no exception — matching the contract with `SpanResolver`: failed
    extractions and ungrounded values still flow through the pipeline, they
    just don't leave a visual trace on the PDF. Zero-area rects are treated
    the same way because PyMuPDF rejects them as highlight quads.
    """

    async def annotate(
        self,
        pdf_bytes: bytes,
        fields: list[ExtractedField],
    ) -> bytes:
        """Annotate a PDF with highlights at each field's bounding boxes.

        PyMuPDF operations are synchronous C calls that block the calling
        thread. ``_annotate_sync`` is offloaded via ``asyncio.to_thread`` so
        the FastAPI event loop stays responsive for concurrent requests,
        mirroring the pattern used by ``DoclingDocumentParser.parse``.
        """
        return await asyncio.to_thread(self._annotate_sync, pdf_bytes, fields)

    @staticmethod
    def _annotate_sync(
        pdf_bytes: bytes,
        fields: list[ExtractedField],
    ) -> bytes:
        """Perform all blocking PyMuPDF work on a worker thread."""
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            doc_any = cast("Any", doc)
            for field in fields:
                for bbox_ref in field.bbox_refs:
                    _draw_highlight(doc_any, bbox_ref)
            return cast("bytes", doc_any.tobytes())


def _draw_highlight(doc: Any, bbox_ref: BoundingBoxRef) -> None:
    # PyMuPDF rejects zero-area quads; mirror the empty-bbox_refs contract and skip silently.
    if bbox_ref.x0 == bbox_ref.x1 or bbox_ref.y0 == bbox_ref.y1:
        return
    page = doc[bbox_ref.page - 1]
    page_height = float(page.rect.height)
    # Flip bottom-left PDF y → top-left MuPDF y. Upper PDF edge (larger y) becomes
    # smaller MuPDF y; lower PDF edge (smaller y) becomes larger MuPDF y.
    y0_mupdf = page_height - bbox_ref.y1
    y1_mupdf = page_height - bbox_ref.y0
    rect = pymupdf.Rect(bbox_ref.x0, y0_mupdf, bbox_ref.x1, y1_mupdf)
    annot = page.add_highlight_annot(rect)
    annot.update()
