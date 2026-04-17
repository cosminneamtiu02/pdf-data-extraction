"""BoundingBox: immutable PDF-page-coordinate rectangle."""

from dataclasses import dataclass


@dataclass(frozen=True)
class BoundingBox:
    """Rectangle in PDF page coordinates (origin bottom-left).

    Coordinates use a **bottom-left** origin, measured in PDF user-space
    points. This matches Docling's ``CoordOrigin.BOTTOMLEFT``, which is the
    convention our upstream parser normalises every provenance box into
    before constructing a ``BoundingBox`` (see
    ``docling_document_parser.py`` — Docling's own default is actually
    ``CoordOrigin.TOPLEFT`` for most pipeline outputs, which the adapter
    flips via ``to_bottom_left_origin(page_height=...)``).

    Note that PyMuPDF's native default is **top-left** origin (the y-axis
    grows downward in ``fitz.Rect``). The annotator therefore converts from
    this bottom-left contract to PyMuPDF's top-left space at draw time; it
    does not consume these coordinates unchanged.
    """

    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        if self.x0 > self.x1:
            msg = f"BoundingBox requires x0 <= x1, got x0={self.x0} x1={self.x1}"
            raise ValueError(msg)
        if self.y0 > self.y1:
            msg = f"BoundingBox requires y0 <= y1, got y0={self.y0} y1={self.y1}"
            raise ValueError(msg)
