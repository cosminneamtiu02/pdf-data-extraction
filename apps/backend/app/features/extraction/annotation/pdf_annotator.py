"""PdfAnnotator: render source-grounded highlights onto the input PDF via PyMuPDF.

This file is the ONLY place in the extraction feature permitted to import
PyMuPDF (`pymupdf` / legacy alias `fitz`). The containment is enforced
mechanically by an AST scan unit test and, from PDFX-E007-F004 onward, by
import-linter. All other layers depend on `ExtractedField` / `BoundingBoxRef`
schemas, never on PyMuPDF types.
"""

from typing import Any, cast

import pymupdf

from app.features.extraction.schemas.bounding_box_ref import BoundingBoxRef
from app.features.extraction.schemas.extracted_field import ExtractedField


class PdfAnnotator:
    """Draw highlight annotations at each `BoundingBoxRef` on the input PDF.

    Fields with empty ``bbox_refs`` are skipped silently — no annotation, no
    warning, no exception — matching the contract with `SpanResolver`: failed
    extractions and ungrounded values still flow through the pipeline, they
    just don't leave a visual trace on the PDF.
    """

    async def annotate(
        self,
        pdf_bytes: bytes,
        fields: list[ExtractedField],
    ) -> bytes:
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
    rect = pymupdf.Rect(bbox_ref.x0, bbox_ref.y0, bbox_ref.x1, bbox_ref.y1)
    annot = page.add_highlight_annot(rect)
    annot.update()
