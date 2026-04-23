"""Harness service — coordinates runs, inference, expected files, export.

Thin wrapper on top of the existing ``ExtractionService``. Persists
results to ``iterations/run-N/results.json`` and per-PDF expected
overrides to ``iterations/run-N/expected/<pdf_id>.json``.
"""

from __future__ import annotations

import json
import shutil
import time
from typing import TYPE_CHECKING, Any

import structlog

from app.features.extraction.schemas.output_mode import OutputMode
from app.harness import paths
from app.harness.schemas import (
    BBox,
    ExpectedFile,
    HarnessMode,
    PdfResult,
    ResultField,
    ResultsFile,
)

if TYPE_CHECKING:
    from pathlib import Path

    from app.features.extraction.schemas.extracted_field import ExtractedField
    from app.features.extraction.service import ExtractionService

_logger = structlog.get_logger(__name__)


def _mode_to_output_mode(mode: HarnessMode) -> OutputMode:
    if mode == "data_only":
        return OutputMode.JSON_ONLY
    return OutputMode.BOTH


def _field_to_result(field: ExtractedField) -> ResultField:
    """Project a pipeline ``ExtractedField`` onto the harness ``ResultField``.

    Takes the first bbox_ref (if any) and converts the PDF-native
    bottom-left ``(x0,y0,x1,y1)`` rectangle into a top-left ``(page,x,y,w,h)``
    shape that the harness UI can pass to pdf.js. We convert y at UI time
    because the harness doesn't know page height here — leave y as
    ``y0`` from the bottom-left space and let the frontend flip it using
    pdf.js' page viewport, which has page height on hand.
    """
    bbox: BBox | None = None
    if field.bbox_refs:
        r = field.bbox_refs[0]
        bbox = BBox(page=r.page, x=r.x0, y=r.y0, w=r.x1 - r.x0, h=r.y1 - r.y0)
    value = field.value if field.value is None else str(field.value)
    return ResultField(name=field.name, value=value, bbox=bbox)


def create_run(mode: HarnessMode, skill_name: str, skill_version: int) -> int:
    """Create ``iterations/run-N``. If N>1, copy forward expected/ from N-1."""
    run_id = paths.next_run_id()
    run_path = paths.run_dir(run_id)
    run_path.mkdir(parents=True, exist_ok=True)
    (run_path / "expected").mkdir(exist_ok=True)
    if mode == "data_and_annotated":
        (run_path / "annotated").mkdir(exist_ok=True)

    if run_id > 1:
        prev_expected = paths.expected_dir(run_id - 1)
        if prev_expected.exists():
            for src in prev_expected.glob("*.json"):
                shutil.copy2(src, run_path / "expected" / src.name)

    # Record the run metadata up front so the skill/mode are persisted even
    # if inference never runs to completion.
    meta = {
        "run_id": run_id,
        "mode": mode,
        "skill_name": skill_name,
        "skill_version": skill_version,
    }
    (run_path / "run.json").write_text(json.dumps(meta, indent=2))

    _logger.info(
        "harness_run_created",
        run_id=run_id,
        mode=mode,
        skill_name=skill_name,
        skill_version=skill_version,
    )
    return run_id


def load_run_meta(run_id: int) -> dict[str, Any]:
    meta_path = paths.run_dir(run_id) / "run.json"
    if not meta_path.exists():
        msg = f"run {run_id} has no run.json — was it created?"
        raise FileNotFoundError(msg)
    return json.loads(meta_path.read_text())


async def run_inference_one(  # noqa: PLR0913 - throwaway harness helper
    service: ExtractionService,
    pdf_id: str,
    mode: HarnessMode,
    skill_name: str,
    skill_version: int,
    run_id: int,
) -> PdfResult:
    """Run the extraction pipeline on one PDF. Persist annotated PDF if mode."""
    pdf_path = paths.source_pdf_path(pdf_id)
    pdf_bytes = pdf_path.read_bytes()

    output_mode = _mode_to_output_mode(mode)
    t0 = time.monotonic()
    try:
        result = await service.extract(
            pdf_bytes=pdf_bytes,
            skill_name=skill_name,
            skill_version=str(skill_version),
            output_mode=output_mode,
        )
    except Exception as exc:  # noqa: BLE001 - harness is best-effort
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        _logger.warning(
            "harness_infer_failed",
            pdf_id=pdf_id,
            run_id=run_id,
            error_class=type(exc).__name__,
            exc_info=True,
        )
        return PdfResult(
            pdf_id=pdf_id,
            fields=[],
            timing_ms=elapsed_ms,
            error=f"{type(exc).__name__}: {exc}",
        )

    elapsed_ms = int((time.monotonic() - t0) * 1000)

    fields = [_field_to_result(f) for f in result.response.fields.values()]

    # Persist annotated PDF if requested
    if mode == "data_and_annotated" and result.annotated_pdf_bytes is not None:
        ann_dir = paths.annotated_dir(run_id)
        ann_dir.mkdir(parents=True, exist_ok=True)
        (ann_dir / f"{pdf_id}.pdf").write_bytes(result.annotated_pdf_bytes)

    return PdfResult(pdf_id=pdf_id, fields=fields, timing_ms=elapsed_ms)


def persist_results(  # noqa: PLR0913 - throwaway harness helper
    run_id: int,
    mode: HarnessMode,
    skill_name: str,
    skill_version: int,
    pdf_results: list[PdfResult],
    total_ms: int,
) -> None:
    results = ResultsFile(
        run_id=run_id,
        mode=mode,
        skill_name=skill_name,
        skill_version=skill_version,
        total_inference_ms=total_ms,
        pdfs=pdf_results,
    )
    paths.results_path(run_id).write_text(results.model_dump_json(indent=2))


def seed_expected_from_results_if_missing(run_id: int, pdf_results: list[PdfResult]) -> None:
    """Run 1 behavior: auto-seed every missing expected/<pdf_id>.json.

    For run N>1 the expected dir was pre-populated by ``create_run``.
    This function only writes files that don't already exist.
    """
    exp_dir = paths.expected_dir(run_id)
    exp_dir.mkdir(parents=True, exist_ok=True)
    for pdf in pdf_results:
        target = exp_dir / f"{pdf.pdf_id}.json"
        if target.exists():
            continue
        expected = ExpectedFile(
            fields=[{"name": f.name, "value": f.value} for f in pdf.fields],  # type: ignore[arg-type]
        )
        target.write_text(expected.model_dump_json(indent=2))


def load_results(run_id: int) -> ResultsFile:
    data = json.loads(paths.results_path(run_id).read_text())
    return ResultsFile.model_validate(data)


def load_expected(run_id: int, pdf_id: str) -> ExpectedFile:
    p = paths.expected_dir(run_id) / f"{pdf_id}.json"
    if not p.exists():
        return ExpectedFile(fields=[])
    data = json.loads(p.read_text())
    return ExpectedFile.model_validate(data)


def save_expected(run_id: int, pdf_id: str, expected: ExpectedFile) -> None:
    exp_dir = paths.expected_dir(run_id)
    exp_dir.mkdir(parents=True, exist_ok=True)
    (exp_dir / f"{pdf_id}.json").write_text(expected.model_dump_json(indent=2))


def build_feedback(
    run_id: int,
    *,
    run_comments: str | None,
    per_pdf_notes: dict[str, str] | None,
) -> dict[str, Any]:
    """Build the ``feedback.json`` shape defined in A5."""
    results = load_results(run_id)
    total_pdfs = len(results.pdfs)
    total_ms = sum(p.timing_ms for p in results.pdfs)
    avg_ms = (total_ms // total_pdfs) if total_pdfs else 0
    per_pdf_notes = per_pdf_notes or {}

    green = yellow = red = 0
    out_pdfs: list[dict[str, Any]] = []
    for pdf in results.pdfs:
        expected = load_expected(run_id, pdf.pdf_id)
        exp_by_name = {e.name: (e.value or "") for e in expected.fields}

        fields_out: list[dict[str, Any]] = []
        any_empty = False
        any_mismatch = False
        # Union of names across output and expected so newly-added expected
        # rows are counted even if the model produced no matching field.
        all_names = {f.name for f in pdf.fields} | set(exp_by_name.keys())
        output_by_name = {f.name: f for f in pdf.fields}
        for name in sorted(all_names):
            out_field = output_by_name.get(name)
            out_val = (out_field.value or "") if out_field else ""
            exp_val = exp_by_name.get(name, "")
            match = out_val == exp_val and out_val != "" and name != ""
            if not (name and out_val and exp_val):
                any_empty = True
            if out_val != exp_val:
                any_mismatch = True
            bbox = out_field.bbox.model_dump() if out_field and out_field.bbox else None
            fields_out.append(
                {
                    "name": name,
                    "output": out_val,
                    "expected": exp_val,
                    "match": match,
                    "bbox": bbox,
                },
            )

        if any_empty:
            status = "red"
            red += 1
        elif any_mismatch:
            status = "yellow"
            yellow += 1
        else:
            status = "green"
            green += 1

        out_pdfs.append(
            {
                "pdf_id": pdf.pdf_id,
                "status": status,
                "inference_ms": pdf.timing_ms,
                "fields": fields_out,
                "notes": per_pdf_notes.get(pdf.pdf_id, ""),
                "error": pdf.error,
            },
        )

    feedback = {
        "run_id": run_id,
        "skill": {"name": results.skill_name, "version": results.skill_version},
        "stats": {
            "total_pdfs": total_pdfs,
            "avg_inference_ms": avg_ms,
            "total_inference_ms": total_ms,
            "green": green,
            "yellow": yellow,
            "red": red,
        },
        "run_comments": run_comments or "",
        "pdfs": out_pdfs,
    }

    paths.feedback_path(run_id).write_text(json.dumps(feedback, indent=2))
    return feedback


def source_pdf(pdf_id: str) -> Path:
    p = paths.source_pdf_path(pdf_id)
    if not p.exists():
        msg = f"source PDF not found: {p}"
        raise FileNotFoundError(msg)
    return p


def annotated_pdf(run_id: int, pdf_id: str) -> Path:
    p = paths.annotated_dir(run_id) / f"{pdf_id}.pdf"
    if not p.exists():
        msg = f"annotated PDF not found: {p}"
        raise FileNotFoundError(msg)
    return p
