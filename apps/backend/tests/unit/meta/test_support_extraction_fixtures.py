"""Meta-tests for ``tests/_support/extraction_fixtures.make_canned_result``.

This test file documents the shared canned-result builder's contract. The
helper itself replaced three near-identical inline copies (issue #404);
the existing contract and integration suites that now import it are the
primary regression net. These cases pin the load-bearing behaviours that
callers rely on so a future tweak cannot silently break the three
callers together.
"""

from __future__ import annotations

from app.features.extraction.extraction_result import ExtractionResult
from app.features.extraction.schemas.bounding_box_ref import BoundingBoxRef
from app.features.extraction.schemas.field_status import FieldStatus
from tests._support.extraction_fixtures import make_canned_result


def test_make_canned_result_defaults_are_happy_path_shape() -> None:
    """Default call yields the single-field `"number"` happy-path result."""
    result = make_canned_result()

    assert isinstance(result, ExtractionResult)
    assert result.annotated_pdf_bytes is None

    response = result.response
    assert response.skill_name == "invoice"
    assert response.skill_version == 1
    assert list(response.fields) == ["number"]
    assert response.metadata.page_count == 1
    assert response.metadata.duration_ms == 500
    assert response.metadata.attempts_per_field == {"number": 1}

    field = response.fields["number"]
    assert field.name == "number"
    assert field.value == "INV-001"
    assert field.status is FieldStatus.extracted
    assert field.grounded is True
    assert len(field.bbox_refs) == 1
    bbox = field.bbox_refs[0]
    assert (bbox.page, bbox.x0, bbox.y0, bbox.x1, bbox.y1) == (1, 10.0, 20.0, 100.0, 30.0)


def test_make_canned_result_field_name_propagates_to_fields_and_attempts() -> None:
    """``field_name`` drives the ``fields`` key and the ``attempts_per_field`` key together."""
    result = make_canned_result(field_name="invoice_number")

    response = result.response
    assert list(response.fields) == ["invoice_number"]
    assert response.fields["invoice_number"].name == "invoice_number"
    assert response.metadata.attempts_per_field == {"invoice_number": 1}


def test_make_canned_result_page_count_is_propagated() -> None:
    """``page_count`` is forwarded to the metadata verbatim."""
    result = make_canned_result(page_count=10)

    assert result.response.metadata.page_count == 10


def test_make_canned_result_annotated_pdf_bytes_are_attached() -> None:
    """An ``annotated_pdf_bytes`` blob survives onto the result untouched."""
    payload = b"%PDF-1.4 annotated"

    result = make_canned_result(annotated_pdf_bytes=payload)

    assert result.annotated_pdf_bytes == payload


def test_make_canned_result_explicit_empty_bbox_refs_ungrounded_field() -> None:
    """Passing ``bbox_refs=[]`` yields an explicitly ungrounded, inferred field."""
    result = make_canned_result(bbox_refs=[])

    field = result.response.fields["number"]
    assert field.bbox_refs == []
    assert field.grounded is False
    assert field.source == "inferred"


def test_make_canned_result_custom_bbox_refs_are_used_as_is() -> None:
    """A caller-supplied ``bbox_refs`` list is preserved without a default prepend."""
    bbox = BoundingBoxRef(page=2, x0=1.0, y0=2.0, x1=3.0, y1=4.0)

    result = make_canned_result(bbox_refs=[bbox])

    assert result.response.fields["number"].bbox_refs == [bbox]
