"""Protocol describing the minimum shape the parser needs from a text item.

This is an internal-to-parsing-package Protocol; adapters in this package
yield objects satisfying it. Callers outside the package never see instances
of this Protocol directly (the parser exposes its own ``TextBlock`` value
type), so the Protocol is module-private (leading-underscore filename).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class DoclingTextItemLike(Protocol):
    """Minimum shape the parser needs from one text-bearing item.

    Adapters expose bounding-box coordinates in PDF page coordinates with the
    origin at the bottom-left of the page (``y0 <= y1``, with ``y`` growing
    upward). Any translation from Docling's native convention lives in the
    adapter, not here.
    """

    @property
    def text(self) -> str: ...
    @property
    def page_number(self) -> int: ...
    @property
    def bbox_x0(self) -> float: ...
    @property
    def bbox_y0(self) -> float: ...
    @property
    def bbox_x1(self) -> float: ...
    @property
    def bbox_y1(self) -> float: ...
