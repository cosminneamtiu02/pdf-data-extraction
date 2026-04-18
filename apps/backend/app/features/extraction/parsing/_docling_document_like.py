"""Protocol describing the minimum shape the parser needs from a Docling document."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.features.extraction.parsing._docling_text_item_like import DoclingTextItemLike


@runtime_checkable
class DoclingDocumentLike(Protocol):
    """Minimum shape the parser needs from a Docling document."""

    @property
    def page_count(self) -> int: ...
    def iter_text_items(self) -> Iterable[DoclingTextItemLike]: ...
