"""Source-grounded bounding box reference for an extracted field."""

from typing import Self

from pydantic import BaseModel, Field, model_validator


class BoundingBoxRef(BaseModel):
    """A single 1-indexed page coordinate rectangle anchoring an extracted value.

    The rectangle is expressed in PDF page coordinates with origin at the
    **bottom-left** of the page (PyMuPDF's default), matching the convention
    locked by PDFX-E003-F001: ``(x0, y0)`` is the bottom-left corner and
    ``(x1, y1)`` is the top-right, so ``x0 <= x1`` and ``y0 <= y1``. Zero-area
    rectangles are legal (a degenerate span is still a grounding anchor);
    inverted rectangles are rejected.
    """

    page: int = Field(ge=1)
    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def _validate_non_inverted(self) -> Self:
        if self.x0 > self.x1:
            msg = f"BoundingBoxRef requires x0 <= x1, got x0={self.x0}, x1={self.x1}"
            raise ValueError(msg)
        if self.y0 > self.y1:
            msg = f"BoundingBoxRef requires y0 <= y1, got y0={self.y0}, y1={self.y1}"
            raise ValueError(msg)
        return self
