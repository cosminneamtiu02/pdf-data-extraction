"""Unit tests for the SpanResolver orchestrator (PDFX-E005-F003).

Every scenario in the feature spec is covered here. The resolver takes a list
of `RawExtraction`s, an `OffsetIndex`, a `ParsedDocument`, and the skill's
declared field names, and returns one `ExtractedField` per declared field in
declared order — enforcing the "every field always present" API invariant.
"""

from structlog.testing import capture_logs

from app.features.extraction.coordinates.offset_index import OffsetIndex
from app.features.extraction.coordinates.offset_index_entry import OffsetIndexEntry
from app.features.extraction.coordinates.span_resolver import SpanResolver
from app.features.extraction.coordinates.sub_block_matcher import SubBlockMatcher
from app.features.extraction.extraction.raw_extraction import RawExtraction
from app.features.extraction.parsing.bounding_box import BoundingBox
from app.features.extraction.parsing.parsed_document import ParsedDocument
from app.features.extraction.parsing.text_block import TextBlock
from app.features.extraction.schemas.field_status import FieldStatus


class _ForbiddenMatcher(SubBlockMatcher):
    """Stub that fails the enclosing test if locate() is invoked.

    Lets tests prove the matcher is NOT consulted on the happy-path
    (direct-offset hit) case, rather than inferring it from the absence
    of a matcher_failed log event — which could also mean the matcher
    was called and succeeded silently.
    """

    def locate(self, block_text: str, value: str) -> None:
        msg = f"SubBlockMatcher.locate should not have been called (block_text={block_text!r}, value={value!r})"
        raise AssertionError(msg)


_DEFAULT_BBOX = (0.0, 0.0, 100.0, 20.0)


def _block(
    *,
    block_id: str,
    text: str,
    page: int = 1,
    bbox: tuple[float, float, float, float] = _DEFAULT_BBOX,
) -> TextBlock:
    x0, y0, x1, y1 = bbox
    return TextBlock(
        text=text,
        page_number=page,
        bbox=BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1),
        block_id=block_id,
    )


def _doc(*blocks: TextBlock, page_count: int | None = None) -> ParsedDocument:
    pages = page_count if page_count is not None else max(b.page_number for b in blocks)
    return ParsedDocument(blocks=tuple(blocks), page_count=pages)


def _index(*entries: tuple[int, int, str]) -> OffsetIndex:
    return OffsetIndex(
        entries=tuple(OffsetIndexEntry(start=s, end=e, block_id=b) for s, e, b in entries)
    )


def test_single_block_sub_block_match_returns_whole_block_bbox() -> None:
    # Interim mitigation for issue #151: the resolver no longer interpolates
    # sub-block positions using character ratios (which drifts for proportional
    # fonts / CJK / emoji / diacritics). A single-block match now returns the
    # whole-block bbox, trading sub-block precision for correctness. When
    # per-glyph geometry is plumbed through TextBlock, tight sub-block bboxes
    # can be reintroduced.
    resolver = SpanResolver()
    block = _block(
        block_id="b0",
        text="Total: $1,847.50 due",
        bbox=(10.0, 100.0, 210.0, 120.0),
    )
    doc = _doc(block)
    index = _index((0, 20, "b0"))
    raw = RawExtraction(
        field_name="total",
        value="$1,847.50",
        char_offset_start=7,
        char_offset_end=16,
        grounded=True,
        attempts=1,
    )

    result = resolver.resolve([raw], index, doc, ["total"])

    assert len(result) == 1
    field = result[0]
    assert field.name == "total"
    assert field.value == "$1,847.50"
    assert field.status == FieldStatus.extracted
    assert field.source == "document"
    assert field.grounded is True
    assert len(field.bbox_refs) == 1
    ref = field.bbox_refs[0]
    assert (ref.page, ref.x0, ref.y0, ref.x1, ref.y1) == (1, 10.0, 100.0, 210.0, 120.0)


def test_tight_sub_block_bbox_falls_back_to_whole_block_when_glyph_geometry_unavailable() -> None:
    # Issue #151: without per-glyph geometry we cannot position a sub-block
    # rectangle over the actual glyphs. For text that mixes narrow and wide
    # glyphs ("il111 MMMW" — narrow chars followed by wide ones under any
    # proportional font), character-ratio interpolation would drift the
    # highlight away from the real position. We return the whole-block bbox
    # instead — correctness over sub-block precision.
    resolver = SpanResolver()
    block = _block(
        block_id="b0",
        text="il111 MMMW",
        bbox=(0.0, 0.0, 100.0, 20.0),
    )
    doc = _doc(block)
    index = _index((0, 10, "b0"))
    raw = RawExtraction(
        field_name="word",
        value="MMMW",
        char_offset_start=6,
        char_offset_end=10,
        grounded=True,
        attempts=1,
    )

    result = resolver.resolve([raw], index, doc, ["word"])

    ref = result[0].bbox_refs[0]
    # Whole-block bbox, NOT the drifty character-ratio interpolation
    # (which would have produced x0=60, x1=100 — clearly missing the "MMMW"
    # glyphs since they occupy the right-hand ~70% of the visual box).
    assert (ref.x0, ref.y0, ref.x1, ref.y1) == (0.0, 0.0, 100.0, 20.0)


def test_single_block_matcher_miss_falls_back_to_whole_block_bbox() -> None:
    resolver = SpanResolver()
    block = _block(block_id="b0", text="Total: $1,847.50 due", bbox=(10, 100, 210, 120))
    doc = _doc(block)
    index = _index((0, 20, "b0"))
    # Value does NOT appear in the block text; offsets still land inside it.
    raw = RawExtraction(
        field_name="total",
        value="NOT_FOUND_STRING",
        char_offset_start=0,
        char_offset_end=5,
        grounded=True,
        attempts=1,
    )

    result = resolver.resolve([raw], index, doc, ["total"])

    field = result[0]
    assert field.grounded is True
    assert len(field.bbox_refs) == 1
    ref = field.bbox_refs[0]
    assert (ref.x0, ref.y0, ref.x1, ref.y1) == (10.0, 100.0, 210.0, 120.0)


def test_matcher_failed_emits_info_log() -> None:
    resolver = SpanResolver()
    block = _block(block_id="b0", text="Total: $1,847.50 due", bbox=(0, 0, 100, 20))
    doc = _doc(block)
    index = _index((0, 20, "b0"))
    raw = RawExtraction(
        field_name="total",
        value="NOT_FOUND",
        char_offset_start=0,
        char_offset_end=5,
        grounded=True,
        attempts=1,
    )

    with capture_logs() as logs:
        resolver.resolve([raw], index, doc, ["total"])

    matcher_events = [e for e in logs if e.get("reason") == "matcher_failed"]
    assert len(matcher_events) == 1
    assert matcher_events[0]["field_name"] == "total"


def test_multi_block_same_page_returns_one_bbox_per_touched_block() -> None:
    resolver = SpanResolver()
    b0 = _block(block_id="b0", text="alpha", bbox=(0, 0, 50, 10))
    b1 = _block(block_id="b1", text="beta", bbox=(0, 20, 50, 30))
    b2 = _block(block_id="b2", text="gamma", bbox=(0, 40, 50, 50))
    doc = _doc(b0, b1, b2)
    # Blocks occupy [0,5) [7,11) [13,18); separator gaps of width 2 between them.
    index = _index((0, 5, "b0"), (7, 11, "b1"), (13, 18, "b2"))
    raw = RawExtraction(
        field_name="word",
        value="alpha\n\nbeta\n\ngamma",
        char_offset_start=2,
        char_offset_end=15,
        grounded=True,
        attempts=1,
    )

    result = resolver.resolve([raw], index, doc, ["word"])

    field = result[0]
    assert field.grounded is True
    assert len(field.bbox_refs) == 3
    # Each ref is the whole-block bbox of b0, b1, b2 respectively.
    assert (field.bbox_refs[0].x0, field.bbox_refs[0].y0) == (0.0, 0.0)
    assert (field.bbox_refs[0].x1, field.bbox_refs[0].y1) == (50.0, 10.0)
    assert (field.bbox_refs[1].x0, field.bbox_refs[1].y0) == (0.0, 20.0)
    assert (field.bbox_refs[1].x1, field.bbox_refs[1].y1) == (50.0, 30.0)
    assert (field.bbox_refs[2].x0, field.bbox_refs[2].y0) == (0.0, 40.0)
    assert (field.bbox_refs[2].x1, field.bbox_refs[2].y1) == (50.0, 50.0)
    assert all(ref.page == 1 for ref in field.bbox_refs)


def test_cross_page_span_returns_bboxes_with_mixed_page_numbers() -> None:
    resolver = SpanResolver()
    b0 = _block(block_id="p2b0", text="first page tail", page=2, bbox=(0, 0, 100, 20))
    b1 = _block(block_id="p3b0", text="second page head", page=3, bbox=(0, 0, 100, 20))
    doc = _doc(b0, b1, page_count=3)
    index = _index((0, 15, "p2b0"), (17, 33, "p3b0"))
    raw = RawExtraction(
        field_name="span",
        value="first page tail\n\nsecond",
        char_offset_start=5,
        char_offset_end=25,
        grounded=True,
        attempts=1,
    )

    result = resolver.resolve([raw], index, doc, ["span"])

    refs = result[0].bbox_refs
    assert len(refs) == 2
    assert refs[0].page == 2
    assert refs[1].page == 3


def test_hallucinated_offsets_outside_any_block_returns_grounded_false() -> None:
    resolver = SpanResolver()
    block = _block(block_id="b0", text="hello", bbox=(0, 0, 50, 10))
    doc = _doc(block)
    index = _index((0, 5, "b0"))
    raw = RawExtraction(
        field_name="x",
        value="hello",
        char_offset_start=99999,
        char_offset_end=100000,
        grounded=True,
        attempts=1,
    )

    result = resolver.resolve([raw], index, doc, ["x"])

    field = result[0]
    assert field.value == "hello"
    assert field.grounded is False
    assert field.bbox_refs == []
    assert field.source == "document"
    assert field.status == FieldStatus.extracted


def test_hallucinated_offsets_in_separator_gap_returns_grounded_false() -> None:
    resolver = SpanResolver()
    b0 = _block(block_id="b0", text="alpha", bbox=(0, 0, 50, 10))
    b1 = _block(block_id="b1", text="beta", bbox=(0, 20, 50, 30))
    doc = _doc(b0, b1)
    # Gap at offsets [5, 7).
    index = _index((0, 5, "b0"), (7, 11, "b1"))
    raw = RawExtraction(
        field_name="x",
        value="alphabet",
        char_offset_start=6,
        char_offset_end=10,
        grounded=True,
        attempts=1,
    )

    result = resolver.resolve([raw], index, doc, ["x"])

    assert result[0].grounded is False
    assert result[0].bbox_refs == []


def test_start_offset_exactly_on_exclusive_end_of_block_returns_grounded_false() -> None:
    # An extraction starting at the exact exclusive end of a block lands in
    # the separator gap — `OffsetIndex.lookup` returns None for any offset
    # `>= entry.end`, so the span is hallucinated even though the value
    # itself is present in a subsequent block.
    resolver = SpanResolver()
    b0 = _block(block_id="b0", text="hello")
    b1 = _block(block_id="b1", text="world")
    doc = _doc(b0, b1)
    index = _index((0, 5, "b0"), (7, 12, "b1"))
    raw = RawExtraction(
        field_name="x",
        value="w",
        char_offset_start=5,
        char_offset_end=6,
        grounded=True,
        attempts=1,
    )

    result = resolver.resolve([raw], index, doc, ["x"])

    assert result[0].grounded is False
    assert result[0].bbox_refs == []


def test_hallucinated_end_offset_past_block_returns_grounded_false() -> None:
    resolver = SpanResolver()
    block = _block(block_id="b0", text="hello")
    doc = _doc(block)
    index = _index((0, 5, "b0"))
    raw = RawExtraction(
        field_name="x",
        value="world",
        char_offset_start=3,
        char_offset_end=99999,
        grounded=True,
        attempts=1,
    )

    result = resolver.resolve([raw], index, doc, ["x"])

    assert result[0].grounded is False
    assert result[0].bbox_refs == []


def test_hallucinated_offsets_emit_info_log() -> None:
    resolver = SpanResolver()
    block = _block(block_id="b0", text="hello")
    doc = _doc(block)
    index = _index((0, 5, "b0"))
    raw = RawExtraction(
        field_name="x",
        value="hello",
        char_offset_start=99999,
        char_offset_end=100000,
        grounded=True,
        attempts=1,
    )

    with capture_logs() as logs:
        resolver.resolve([raw], index, doc, ["x"])

    halluc_events = [e for e in logs if e.get("reason") == "hallucinated_offsets"]
    assert len(halluc_events) == 1
    assert halluc_events[0]["field_name"] == "x"


def test_ungrounded_world_knowledge_returns_inferred_source() -> None:
    resolver = SpanResolver()
    doc = _doc(_block(block_id="b0", text="irrelevant"))
    index = _index((0, 10, "b0"))
    raw = RawExtraction(
        field_name="country",
        value="United States",
        char_offset_start=None,
        char_offset_end=None,
        grounded=False,
        attempts=1,
    )

    result = resolver.resolve([raw], index, doc, ["country"])

    field = result[0]
    assert field.name == "country"
    assert field.value == "United States"
    assert field.status == FieldStatus.extracted
    assert field.source == "inferred"
    assert field.grounded is False
    assert field.bbox_refs == []


def test_ungrounded_world_knowledge_emits_info_log() -> None:
    resolver = SpanResolver()
    doc = _doc(_block(block_id="b0", text="irrelevant"))
    index = _index((0, 10, "b0"))
    raw = RawExtraction(
        field_name="country",
        value="United States",
        char_offset_start=None,
        char_offset_end=None,
        grounded=False,
        attempts=1,
    )

    with capture_logs() as logs:
        resolver.resolve([raw], index, doc, ["country"])

    ungrounded_events = [e for e in logs if e.get("reason") == "ungrounded"]
    assert len(ungrounded_events) == 1
    assert ungrounded_events[0]["field_name"] == "country"


def test_failed_placeholder_value_none_returns_status_failed() -> None:
    resolver = SpanResolver()
    doc = _doc(_block(block_id="b0", text="abc"))
    index = _index((0, 3, "b0"))
    raw = RawExtraction(
        field_name="invoice_date",
        value=None,
        char_offset_start=None,
        char_offset_end=None,
        grounded=False,
        attempts=1,
    )

    result = resolver.resolve([raw], index, doc, ["invoice_date"])

    field = result[0]
    assert field.name == "invoice_date"
    assert field.value is None
    assert field.status == FieldStatus.failed
    assert field.source == "document"
    assert field.grounded is False
    assert field.bbox_refs == []


def test_missing_declared_field_synthesized_as_failed() -> None:
    resolver = SpanResolver()
    doc = _doc(_block(block_id="b0", text="alpha"))
    index = _index((0, 5, "b0"))
    raw_a = RawExtraction(
        field_name="a",
        value="alpha",
        char_offset_start=0,
        char_offset_end=5,
        grounded=True,
        attempts=1,
    )
    raw_c = RawExtraction(
        field_name="c",
        value="ginger",
        char_offset_start=None,
        char_offset_end=None,
        grounded=False,
        attempts=1,
    )

    result = resolver.resolve([raw_a, raw_c], index, doc, ["a", "b", "c"])

    assert [f.name for f in result] == ["a", "b", "c"]
    b_field = result[1]
    assert b_field.name == "b"
    assert b_field.value is None
    assert b_field.status == FieldStatus.failed
    assert b_field.source == "document"
    assert b_field.grounded is False
    assert b_field.bbox_refs == []


def test_output_order_matches_declared_fields_not_raw_extractions() -> None:
    resolver = SpanResolver()
    doc = _doc(_block(block_id="b0", text="hello"))
    index = _index((0, 5, "b0"))
    raw_b = RawExtraction(
        field_name="b",
        value="bee",
        char_offset_start=None,
        char_offset_end=None,
        grounded=False,
        attempts=1,
    )
    raw_a = RawExtraction(
        field_name="a",
        value="ay",
        char_offset_start=None,
        char_offset_end=None,
        grounded=False,
        attempts=1,
    )

    result = resolver.resolve([raw_b, raw_a], index, doc, ["a", "b"])

    assert [f.name for f in result] == ["a", "b"]
    assert result[0].value == "ay"
    assert result[1].value == "bee"


def test_duplicate_raw_extraction_first_wins() -> None:
    resolver = SpanResolver()
    doc = _doc(_block(block_id="b0", text="hello"))
    index = _index((0, 5, "b0"))
    first = RawExtraction(
        field_name="a",
        value="first",
        char_offset_start=None,
        char_offset_end=None,
        grounded=False,
        attempts=1,
    )
    second = RawExtraction(
        field_name="a",
        value="second",
        char_offset_start=None,
        char_offset_end=None,
        grounded=False,
        attempts=1,
    )

    result = resolver.resolve([first, second], index, doc, ["a"])

    assert len(result) == 1
    assert result[0].value == "first"


def test_raw_extraction_for_undeclared_field_is_dropped() -> None:
    resolver = SpanResolver()
    doc = _doc(_block(block_id="b0", text="hello"))
    index = _index((0, 5, "b0"))
    declared = RawExtraction(
        field_name="a",
        value="ay",
        char_offset_start=None,
        char_offset_end=None,
        grounded=False,
        attempts=1,
    )
    extra = RawExtraction(
        field_name="xtra",
        value="nope",
        char_offset_start=None,
        char_offset_end=None,
        grounded=False,
        attempts=1,
    )

    result = resolver.resolve([declared, extra], index, doc, ["a"])

    assert len(result) == 1
    assert result[0].name == "a"


def test_empty_declared_fields_and_empty_raw_extractions_returns_empty_list() -> None:
    resolver = SpanResolver()
    doc = _doc(_block(block_id="b0", text="hello"))
    index = _index((0, 5, "b0"))

    result = resolver.resolve([], index, doc, [])

    assert result == []


def test_empty_declared_fields_with_nonempty_raw_extractions_returns_empty() -> None:
    resolver = SpanResolver()
    doc = _doc(_block(block_id="b0", text="hello"))
    index = _index((0, 5, "b0"))
    raw = RawExtraction(
        field_name="ignored",
        value="ignored",
        char_offset_start=None,
        char_offset_end=None,
        grounded=False,
        attempts=1,
    )

    result = resolver.resolve([raw], index, doc, [])

    assert result == []


def test_empty_raw_extractions_with_declared_fields_returns_all_failed() -> None:
    resolver = SpanResolver()
    doc = _doc(_block(block_id="b0", text="hello"))
    index = _index((0, 5, "b0"))

    result = resolver.resolve([], index, doc, ["a", "b"])

    assert [f.name for f in result] == ["a", "b"]
    assert all(f.status == FieldStatus.failed for f in result)
    assert all(f.value is None for f in result)


def test_resolver_does_not_mutate_inputs() -> None:
    resolver = SpanResolver()
    block = _block(block_id="b0", text="alpha")
    doc = _doc(block)
    index = _index((0, 5, "b0"))
    raw = RawExtraction(
        field_name="a",
        value="alpha",
        char_offset_start=0,
        char_offset_end=5,
        grounded=True,
        attempts=1,
    )
    raws = [raw]
    declared = ["a"]

    resolver.resolve(raws, index, doc, declared)

    assert raws == [raw]
    assert declared == ["a"]
    assert doc.blocks == (block,)
    assert index.entries[0].block_id == "b0"


def test_sub_block_bbox_at_full_block_boundary_equals_whole_block() -> None:
    resolver = SpanResolver()
    block = _block(block_id="b0", text="hello", bbox=(10, 100, 60, 120))
    doc = _doc(block)
    index = _index((0, 5, "b0"))
    raw = RawExtraction(
        field_name="a",
        value="hello",
        char_offset_start=0,
        char_offset_end=5,
        grounded=True,
        attempts=1,
    )

    result = resolver.resolve([raw], index, doc, ["a"])

    ref = result[0].bbox_refs[0]
    assert (ref.x0, ref.y0, ref.x1, ref.y1) == (10.0, 100.0, 60.0, 120.0)


def test_sub_block_bbox_preserves_full_block_vertical_extent() -> None:
    resolver = SpanResolver()
    block = _block(block_id="b0", text="abcdefghij", bbox=(0, 50, 100, 70))
    doc = _doc(block)
    index = _index((0, 10, "b0"))
    raw = RawExtraction(
        field_name="a",
        value="cde",
        char_offset_start=2,
        char_offset_end=5,
        grounded=True,
        attempts=1,
    )

    result = resolver.resolve([raw], index, doc, ["a"])

    ref = result[0].bbox_refs[0]
    assert ref.y0 == 50.0
    assert ref.y1 == 70.0


def test_repeated_value_in_block_matches_offset_range_without_matcher_invocation() -> None:
    # "A=42 B=42" — LangExtract reports offsets for the second "42" (positions
    # 7..9 in the block). SubBlockMatcher.locate would return the FIRST "42"
    # at positions 2..4. The resolver must use the offset-reported range as
    # the authoritative match (avoiding the matcher fallback), and — per the
    # issue #151 interim mitigation — emit the whole-block bbox.
    # Inject a spy matcher that raises if invoked. Proves the happy-path
    # branch never consults the matcher — stronger than a log-absence
    # assertion, which could pass even if the matcher was called and
    # succeeded silently.
    resolver = SpanResolver(matcher=_ForbiddenMatcher())
    block = _block(block_id="b0", text="A=42 B=42", bbox=(0, 0, 100, 20))
    doc = _doc(block)
    index = _index((0, 9, "b0"))
    raw = RawExtraction(
        field_name="amount",
        value="42",
        char_offset_start=7,
        char_offset_end=9,
        grounded=True,
        attempts=1,
    )

    result = resolver.resolve([raw], index, doc, ["amount"])

    ref = result[0].bbox_refs[0]
    assert (ref.x0, ref.x1) == (0.0, 100.0)
    assert (ref.y0, ref.y1) == (0.0, 20.0)


def test_repeated_value_first_occurrence_also_resolves_without_matcher_invocation() -> None:
    # Same block "A=42 B=42" but offsets point to the first "42" (2..4).
    resolver = SpanResolver()
    block = _block(block_id="b0", text="A=42 B=42", bbox=(0, 0, 90, 20))
    doc = _doc(block)
    index = _index((0, 9, "b0"))
    raw = RawExtraction(
        field_name="amount",
        value="42",
        char_offset_start=2,
        char_offset_end=4,
        grounded=True,
        attempts=1,
    )

    result = resolver.resolve([raw], index, doc, ["amount"])

    ref = result[0].bbox_refs[0]
    # Whole-block bbox (issue #151 interim mitigation).
    assert (ref.x0, ref.x1) == (0.0, 90.0)
    assert (ref.y0, ref.y1) == (0.0, 20.0)


def test_multi_block_span_skips_zero_width_empty_blocks() -> None:
    # If an empty block (zero-width index entry) sits between two real blocks,
    # the resolver must not emit a bbox for it since no character belongs to
    # that entry.
    resolver = SpanResolver()
    b0 = _block(block_id="b0", text="alpha", bbox=(0, 0, 50, 10))
    b_empty = _block(block_id="b_empty", text="", bbox=(0, 15, 50, 15))
    b1 = _block(block_id="b1", text="beta", bbox=(0, 20, 50, 30))
    doc = _doc(b0, b_empty, b1)
    # b_empty gets a zero-width entry at offset 7.
    index = _index((0, 5, "b0"), (7, 7, "b_empty"), (9, 13, "b1"))
    raw = RawExtraction(
        field_name="x",
        value="alpha\n\n\n\nbeta",
        char_offset_start=2,
        char_offset_end=11,
        grounded=True,
        attempts=1,
    )

    result = resolver.resolve([raw], index, doc, ["x"])

    refs = result[0].bbox_refs
    assert len(refs) == 2
    assert refs[0].y0 == 0.0
    assert refs[1].y0 == 20.0


def test_happy_path_emits_no_span_resolver_logs() -> None:
    resolver = SpanResolver()
    block = _block(block_id="b0", text="Total: $1,847.50 due", bbox=(0, 0, 200, 20))
    doc = _doc(block)
    index = _index((0, 20, "b0"))
    raw = RawExtraction(
        field_name="total",
        value="$1,847.50",
        char_offset_start=7,
        char_offset_end=16,
        grounded=True,
        attempts=1,
    )

    with capture_logs() as logs:
        resolver.resolve([raw], index, doc, ["total"])

    span_events = [e for e in logs if "reason" in e and "field_name" in e]
    assert span_events == []
