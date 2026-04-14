"""BoundingBox: immutable PDF-page-coordinate rectangle."""

from dataclasses import dataclass


@dataclass(frozen=True)
class BoundingBox:
    """Rectangle in PDF page coordinates (origin bottom-left).

    Coordinate convention matches PyMuPDF's default so the annotator does not need
    to transform coordinates downstream.
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
