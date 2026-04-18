"""SpanResolver: orchestrate raw extractions into grounded ExtractedFields."""

from typing import cast

import structlog

from app.features.extraction.coordinates.char_range import CharRange
from app.features.extraction.coordinates.offset_index import OffsetIndex
from app.features.extraction.coordinates.sub_block_matcher import SubBlockMatcher
from app.features.extraction.extraction.raw_extraction import RawExtraction
from app.features.extraction.parsing.parsed_document import ParsedDocument
from app.features.extraction.parsing.text_block import TextBlock
from app.features.extraction.schemas.bounding_box_ref import BoundingBoxRef
from app.features.extraction.schemas.extracted_field import ExtractedField
from app.features.extraction.schemas.field_status import FieldStatus

_logger = structlog.get_logger(__name__)


class SpanResolver:
    """Top-level orchestrator for the coordinate matching layer.

    Consumes the three artifacts produced by previous features — an
    `OffsetIndex`, a `ParsedDocument`, and a list of `RawExtraction`s — and
    returns one `ExtractedField` per name in `declared_fields`, in declared
    order. The "every declared field always present" invariant is enforced
    here: extractions that the LLM never produced are synthesized as failed
    placeholders so the response shape is deterministic.

    Six cases are handled uniformly:

    1. `value is None`                 → status=failed, grounded=False.
    2. `grounded is False`              → status=extracted, source=inferred,
                                          grounded=False.
    3. Single-block span, offsets hit   → status=extracted, source=document,
                                          grounded=True, one whole-block
                                          BoundingBoxRef. Covers both the
                                          direct-offset happy path (the
                                          reported slice `block.text[start:end]`
                                          equals `value`) and the matcher-hit
                                          fallback (SubBlockMatcher.locate
                                          recovers a range when the offsets
                                          drift). (Tight sub-block
                                          interpolation was removed per
                                          issue #151; future glyph-level
                                          geometry will re-enable tight
                                          bboxes.)
    4. Single-block span, matcher miss  → logs the matcher failure; the
                                          bbox output is the same whole-block
                                          BoundingBoxRef as case 3.
    5. Multi-block / cross-page span    → one whole-block BoundingBoxRef per
                                          touched block, all with grounded=True.
    6. Offsets outside any block        → grounded=False with empty bbox_refs
                                          (hallucinated offsets).

    The resolver does not mutate any input and guarantees no ExtractedField
    is emitted twice: if `raw_extractions` contains duplicates for the same
    field name, the first occurrence wins and subsequent ones are dropped.
    """

    def __init__(self, matcher: SubBlockMatcher | None = None) -> None:
        self._matcher = matcher if matcher is not None else SubBlockMatcher()

    def resolve(
        self,
        raw_extractions: list[RawExtraction],
        offset_index: OffsetIndex,
        parsed_document: ParsedDocument,
        declared_fields: list[str],
    ) -> list[ExtractedField]:
        blocks_by_id: dict[str, TextBlock] = {
            block.block_id: block for block in parsed_document.blocks
        }
        raws_by_name: dict[str, RawExtraction] = {}
        for raw in raw_extractions:
            raws_by_name.setdefault(raw.field_name, raw)

        return [
            self._resolve_one(raws_by_name[name], offset_index, blocks_by_id)
            if name in raws_by_name
            else _synthesize_missing(name)
            for name in declared_fields
        ]

    def _resolve_one(
        self,
        raw: RawExtraction,
        offset_index: OffsetIndex,
        blocks_by_id: dict[str, TextBlock],
    ) -> ExtractedField:
        if raw.value is None:
            return ExtractedField(
                name=raw.field_name,
                value=None,
                status=FieldStatus.failed,
                source="document",
                grounded=False,
                bbox_refs=[],
            )

        if not raw.grounded:
            _logger.info(
                "span_resolver_ungrounded",
                field_name=raw.field_name,
                reason="ungrounded",
            )
            return ExtractedField(
                name=raw.field_name,
                value=raw.value,
                status=FieldStatus.extracted,
                source="inferred",
                grounded=False,
                bbox_refs=[],
            )

        # `RawExtraction.__post_init__` guarantees both offsets are set and
        # strictly ordered when `grounded` is True, so the casts below are
        # type-level narrowing that carries no runtime cost.
        start_offset = cast("int", raw.char_offset_start)
        end_offset = cast("int", raw.char_offset_end)

        start_lookup = offset_index.lookup(start_offset)
        end_lookup = offset_index.lookup(end_offset - 1)
        if start_lookup is None or end_lookup is None:
            _logger.info(
                "span_resolver_hallucinated_offsets",
                field_name=raw.field_name,
                reason="hallucinated_offsets",
            )
            return ExtractedField(
                name=raw.field_name,
                value=raw.value,
                status=FieldStatus.extracted,
                source="document",
                grounded=False,
                bbox_refs=[],
            )

        start_block_id, local_start = start_lookup
        end_block_id, local_end_inclusive = end_lookup

        if start_block_id == end_block_id:
            local_end = local_end_inclusive + 1
            bbox_refs = [
                self._resolve_single_block(
                    raw, blocks_by_id[start_block_id], local_start, local_end
                )
            ]
        else:
            bbox_refs = _collect_multi_block_bboxes(
                start_block_id=start_block_id,
                end_block_id=end_block_id,
                offset_index=offset_index,
                blocks_by_id=blocks_by_id,
            )

        return ExtractedField(
            name=raw.field_name,
            value=raw.value,
            status=FieldStatus.extracted,
            source="document",
            grounded=True,
            bbox_refs=bbox_refs,
        )

    def _resolve_single_block(
        self,
        raw: RawExtraction,
        block: TextBlock,
        local_start: int,
        local_end: int,
    ) -> BoundingBoxRef:
        value_str = str(raw.value)

        # Prefer the offset-reported range when it directly matches the
        # value in the block text. This handles repeated values correctly
        # (e.g. "A=42 B=42" where the offsets point to the second "42").
        if block.text[local_start:local_end] == value_str:
            return _tight_sub_block_bbox(block, CharRange(start=local_start, end=local_end))

        # Offset drift (whitespace/Unicode normalization differences between
        # the concatenated text and the block's own text): fall back to the
        # three-step SubBlockMatcher which tolerates these.
        match = self._matcher.locate(block.text, value_str)
        if match is not None:
            return _tight_sub_block_bbox(block, match)

        _logger.info(
            "span_resolver_matcher_failed",
            field_name=raw.field_name,
            reason="matcher_failed",
        )
        return _whole_block_bbox(block)


def _synthesize_missing(name: str) -> ExtractedField:
    return ExtractedField(
        name=name,
        value=None,
        status=FieldStatus.failed,
        source="document",
        grounded=False,
        bbox_refs=[],
    )


def _whole_block_bbox(block: TextBlock) -> BoundingBoxRef:
    return BoundingBoxRef(
        page=block.page_number,
        x0=block.bbox.x0,
        y0=block.bbox.y0,
        x1=block.bbox.x1,
        y1=block.bbox.y1,
    )


def _tight_sub_block_bbox(block: TextBlock, match: CharRange) -> BoundingBoxRef:
    # Issue #151 interim mitigation: we used to interpolate a sub-block
    # rectangle via character-offset ratios, which assumes monospaced text and
    # drifts for proportional fonts / CJK / emoji / diacritics. Until per-cell
    # glyph geometry is plumbed through TextBlock from Docling, fall back to
    # the whole-block bbox — correctness over sub-block precision. The `match`
    # argument is retained so callers and the function signature stay stable
    # for the future glyph-aware implementation.
    del match  # unused until glyph-level geometry is available
    return _whole_block_bbox(block)


def _collect_multi_block_bboxes(
    *,
    start_block_id: str,
    end_block_id: str,
    offset_index: OffsetIndex,
    blocks_by_id: dict[str, TextBlock],
) -> list[BoundingBoxRef]:
    refs: list[BoundingBoxRef] = []
    in_span = False
    for entry in offset_index.entries:
        if entry.block_id == start_block_id:
            in_span = True
        if in_span and entry.start < entry.end:
            refs.append(_whole_block_bbox(blocks_by_id[entry.block_id]))
        if entry.block_id == end_block_id:
            break
    return refs
