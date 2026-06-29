from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.database import get_db
from marquee.models import PipelineRun
from marquee.pipeline.ocr_label_capture import (
    OcrLabelCaptureError,
    capture_ocr_label,
    clear_ocr_labels,
    list_ocr_labels,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dev/ocr-labels", tags=["dev"])


class OcrLabelRequest(BaseModel):
    run_id: str
    orig_filename: str


@router.post("/false-rejection")
async def mark_false_rejection(
    body: OcrLabelRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    run = await _load_run(db, body.run_id)
    return await _capture(run, body.orig_filename, "false_rejection")


@router.post("/false-acceptance")
async def mark_false_acceptance(
    body: OcrLabelRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    run = await _load_run(db, body.run_id)
    return await _capture(run, body.orig_filename, "false_acceptance")


@router.post("/clear")
async def clear_labels():
    return await asyncio.to_thread(clear_ocr_labels)


@router.get("/run/{run_id}")
async def get_run_labels(
    run_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await _load_run(db, run_id)
    return await asyncio.to_thread(list_ocr_labels, run_id)


async def _load_run(db: AsyncSession, run_id: str) -> PipelineRun:
    run = (await db.execute(select(PipelineRun).where(PipelineRun.run_id == run_id))).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return run


async def _capture(run: PipelineRun, orig_filename: str, label_kind: str):
    try:
        result = await asyncio.to_thread(capture_ocr_label, run, orig_filename, label_kind)
    except OcrLabelCaptureError as exc:
        logger.info(
            "OCR LABEL CAPTURE REJECTED | run_id=%s | file=%s | kind=%s | %s",
            run.run_id,
            orig_filename,
            label_kind,
            exc,
        )
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {
        "status": "captured",
        "label_kind": result.label_kind,
        "path": str(result.capture_dir),
        "image_copied": result.image_copied,
        "log_captured": result.log_captured,
        "missing_artifacts": list(result.missing_artifacts),
        "metadata": result.metadata,
    }
