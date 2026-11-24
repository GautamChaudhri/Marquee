"""Run one subject through the real pipeline and leave a complete run folder.

This is the function both ``marquee run`` and, from M5, the run job call. It builds the
feature extractor in personalized mode, attaches the run log, awaits the orchestrator with
TMDB as the candidate source, and writes ``pipeline_run.json``. A residual artifact and its
runtime context pass through to the orchestrator unchanged; without them the weighted scorer
ranks.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from marquee.pipeline import runner
from marquee.pipeline.features import FeatureExtractor
from marquee.pipeline.orchestrator import (
    PosterPipelineOutput,
    PosterSourceInput,
    PosterSubjectInput,
    run_poster_pipeline,
)
from marquee.pipeline.runner import ProgressCallback, ShouldCancel
from marquee.pipeline.scorer import ResidualRuntimeContext
from marquee.pipeline.text_profiles import OcrGateContext

RUN_LOG = "pipeline.log"
RUN_ARCHIVE = "pipeline_run.json"


async def run_single_subject(
    *,
    subject: PosterSubjectInput,
    run_dir: Path,
    ocr_gate: OcrGateContext,
    progress: ProgressCallback | None = None,
    should_cancel: ShouldCancel | None = None,
    run_id: str | None = None,
    feature_extractor: FeatureExtractor | None = None,
    residual_path: Path | None = None,
    residual_context: ResidualRuntimeContext | None = None,
) -> PosterPipelineOutput:
    """Run ``subject`` inside ``run_dir`` (created here; it must not exist yet)."""
    run_dir.mkdir(parents=True, exist_ok=False)
    # The run log is attached first so it also records the extractor's preflight
    # decisions (which optional features are active, and why).
    handler = runner._add_run_file_handler(run_dir / RUN_LOG)
    try:
        extractor = feature_extractor or FeatureExtractor(personalization_mode="personalized")
        await asyncio.to_thread(extractor.preflight)
        output = await run_poster_pipeline(
            subject=subject,
            source=PosterSourceInput(mode="tmdb"),
            out_dir=run_dir,
            feature_extractor=extractor,
            ocr_gate=ocr_gate,
            progress=progress,
            should_cancel=should_cancel,
            run_id=run_id,
            residual_path=residual_path,
            residual_context=residual_context,
            personalization_mode="personalized",
        )
        runner.write_run_json(run_dir / RUN_ARCHIVE, output.payload)
    finally:
        runner._remove_run_file_handler(handler)
    return output
