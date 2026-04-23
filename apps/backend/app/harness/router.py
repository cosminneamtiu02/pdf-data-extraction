"""Harness HTTP router — mounted under ``/harness``.

Every endpoint is throwaway iteration tooling. No contract guarantees,
no CLAUDE.md purity. ``HTTPException`` is used directly (CLAUDE.md bans
it elsewhere but the harness is explicitly exempt).
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from typing import TYPE_CHECKING, Annotated, Any

import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from app.api.deps import get_extraction_service
from app.features.extraction.service import ExtractionService  # noqa: TC001  # FastAPI DI
from app.harness import paths, service
from app.harness.schemas import (
    CreateRunRequest,
    CreateRunResponse,
    ExpectedFile,
    ExportRequest,
    PdfListItem,
    PdfResult,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/harness", tags=["harness"])


@router.post("/runs", response_model=CreateRunResponse)
async def create_run(body: CreateRunRequest) -> CreateRunResponse:
    run_id = service.create_run(body.mode, body.skill_name, body.skill_version)
    return CreateRunResponse(run_id=run_id)


@router.get("/runs/{run_id}/pdfs", response_model=list[PdfListItem])
async def list_pdfs(run_id: int) -> list[PdfListItem]:  # noqa: ARG001 - run_id unused here but part of REST shape
    return [PdfListItem(pdf_id=pid) for pid in paths.list_pdf_ids()]


@router.post("/runs/{run_id}/infer")
async def infer(
    run_id: int,
    extraction: Annotated[ExtractionService, Depends(get_extraction_service)],
) -> StreamingResponse:
    """SSE stream of inference progress.

    Emits one ``data: <json>\\n\\n`` event per PDF plus a final ``done``
    event carrying aggregate timing. The frontend consumes this with
    ``EventSource``.
    """
    try:
        meta = service.load_run_meta(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    mode = meta["mode"]
    skill_name = meta["skill_name"]
    skill_version = meta["skill_version"]

    pdf_ids = paths.list_pdf_ids()

    async def stream() -> AsyncIterator[bytes]:
        yield _sse({"type": "start", "total": len(pdf_ids)})
        t0 = time.monotonic()
        pdf_results: list[PdfResult] = []
        for i, pdf_id in enumerate(pdf_ids):
            yield _sse(
                {"type": "pdf_start", "pdf_id": pdf_id, "index": i, "total": len(pdf_ids)},
            )
            pdf_result = await service.run_inference_one(
                extraction,
                pdf_id=pdf_id,
                mode=mode,
                skill_name=skill_name,
                skill_version=skill_version,
                run_id=run_id,
            )
            pdf_results.append(pdf_result)
            yield _sse(
                {
                    "type": "pdf_done",
                    "pdf_id": pdf_id,
                    "index": i,
                    "total": len(pdf_ids),
                    "timing_ms": pdf_result.timing_ms,
                    "error": pdf_result.error,
                },
            )

        total_ms = int((time.monotonic() - t0) * 1000)
        service.persist_results(
            run_id=run_id,
            mode=mode,
            skill_name=skill_name,
            skill_version=skill_version,
            pdf_results=pdf_results,
            total_ms=total_ms,
        )
        # Run-1 auto-seed: for any PDF without an expected file, write one
        # equal to the inference output.
        service.seed_expected_from_results_if_missing(run_id, pdf_results)

        yield _sse(
            {
                "type": "done",
                "total": len(pdf_ids),
                "total_ms": total_ms,
                "avg_ms": (total_ms // len(pdf_ids)) if pdf_ids else 0,
            },
        )

    return StreamingResponse(stream(), media_type="text/event-stream")


def _sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


@router.get("/runs/{run_id}/results/{pdf_id}")
async def get_result(run_id: int, pdf_id: str) -> JSONResponse:
    try:
        results = service.load_results(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    for p in results.pdfs:
        if p.pdf_id == pdf_id:
            return JSONResponse(
                {
                    "fields": [f.model_dump() for f in p.fields],
                    "timing_ms": p.timing_ms,
                    "error": p.error,
                },
            )
    raise HTTPException(status_code=404, detail=f"pdf {pdf_id} not in run {run_id}")


@router.get("/runs/{run_id}/pdfs/{pdf_id}/source")
async def get_source_pdf(run_id: int, pdf_id: str) -> FileResponse:  # noqa: ARG001 - run_id is part of REST shape
    try:
        p = service.source_pdf(pdf_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(p, media_type="application/pdf")


@router.get("/runs/{run_id}/pdfs/{pdf_id}/annotated")
async def get_annotated_pdf(run_id: int, pdf_id: str) -> FileResponse:
    try:
        p = service.annotated_pdf(run_id, pdf_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(p, media_type="application/pdf")


@router.get("/runs/{run_id}/expected/{pdf_id}", response_model=ExpectedFile)
async def get_expected(run_id: int, pdf_id: str) -> ExpectedFile:
    return service.load_expected(run_id, pdf_id)


@router.put("/runs/{run_id}/expected/{pdf_id}", response_model=ExpectedFile)
async def put_expected(
    run_id: int,
    pdf_id: str,
    body: Annotated[ExpectedFile, Body()],
) -> ExpectedFile:
    service.save_expected(run_id, pdf_id, body)
    return body


@router.post("/runs/{run_id}/export")
async def export(run_id: int, body: ExportRequest) -> Response:
    feedback = service.build_feedback(
        run_id,
        run_comments=body.run_comments,
        per_pdf_notes=body.per_pdf_notes,
    )

    if not body.include_pdfs:
        return JSONResponse(
            feedback,
            headers={
                "Content-Disposition": f'attachment; filename="feedback-run-{run_id}.json"',
            },
        )

    # Zip: feedback.json at root + every source PDF under pdfs/<id>.pdf
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("feedback.json", json.dumps(feedback, indent=2))
        for pdf_id in paths.list_pdf_ids():
            src = paths.source_pdf_path(pdf_id)
            if src.exists():
                zf.write(src, f"pdfs/{pdf_id}.pdf")
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="feedback-run-{run_id}.zip"',
        },
    )
