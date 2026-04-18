"""Frozen dataclass implementation of ``DoclingTextItemLike``.

The real-Docling adapter yields instances of this class to bridge Docling's
own types into the parser's Protocol shape. It is a frozen dataclass so
callers cannot mutate a yielded item while the parser is still walking the
document.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FlatDoclingTextItem:
    text: str
    page_number: int
    bbox_x0: float
    bbox_y0: float
    bbox_x1: float
    bbox_y1: float
