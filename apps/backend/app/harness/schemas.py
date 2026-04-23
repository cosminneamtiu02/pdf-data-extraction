"""Harness request/response Pydantic schemas.

Kept in one file because this is throwaway iteration tooling, not the
main extraction feature. The "one class per file" rule is waived for
the harness (see the harness brief).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# The harness lets the user pick between two run modes. Internally they
# map onto ``OutputMode.JSON_ONLY`` / ``OutputMode.BOTH``.
HarnessMode = Literal["data_only", "data_and_annotated"]


class CreateRunRequest(BaseModel):
    mode: HarnessMode
    skill_name: str
    skill_version: int


class CreateRunResponse(BaseModel):
    run_id: int


class PdfListItem(BaseModel):
    pdf_id: str


class BBox(BaseModel):
    page: int
    x: float
    y: float
    w: float
    h: float


class ResultField(BaseModel):
    name: str
    value: str | None = None
    bbox: BBox | None = None


class PdfResult(BaseModel):
    """Per-PDF inference result as stored in ``results.json``."""

    pdf_id: str
    fields: list[ResultField]
    timing_ms: int
    error: str | None = None


class ResultsFile(BaseModel):
    """Top-level shape of ``results.json``."""

    run_id: int
    mode: HarnessMode
    skill_name: str
    skill_version: int
    total_inference_ms: int
    pdfs: list[PdfResult]


class ExpectedField(BaseModel):
    name: str
    value: str | None = None


class ExpectedFile(BaseModel):
    """Human-edited expected-output JSON (one per PDF)."""

    fields: list[ExpectedField]


class ExportRequest(BaseModel):
    include_pdfs: bool = False
    run_comments: str | None = None
    per_pdf_notes: dict[str, str] | None = None
