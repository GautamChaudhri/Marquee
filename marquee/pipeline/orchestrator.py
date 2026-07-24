"""Compose the real poster-pipeline stages into one confined run (JMC6H H2).

This is deliberately *not* a second pipeline. It wires together the established
stage functions in :mod:`marquee.pipeline.runner` — fetch/download (or a confined
fixture source), the synchronous SHA/gate/style/OCR/detail/rank stages, ranked
output placement, and the run payload — behind one call the contained internal
runner performs inside the attempt process group/cgroup.

The orchestrator writes only inside ``out_dir`` (the attempt workspace). It never
touches the canonical database or a library destination; the coordinator/handler
turns its produced files and summary into artifacts and a ``PipelineRun``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from marquee.core.poster_sources.tmdb import PosterCandidate, TMDBClient
from marquee.core.text_profiles import OcrGateContext
from marquee.models import Movie
from marquee.pipeline.features import FeatureExtractor
from marquee.pipeline.runner import (
    ProgressCallback,
    ShouldCancel,
    _root_images,
    build_run_payload,
    fetch_and_download,
    place_outputs,
    run_sync_stages,
)
from marquee.pipeline.scorer import ResidualRuntimeContext, select_scorer
from marquee.pipeline.types import CandidateScore

_FIXTURE_SUBDIR = "candidates"


@dataclass(frozen=True, slots=True)
class PosterSubjectInput:
    """The immutable subject a poster run analyzes."""

    title: str
    media_type: str = "movie"
    movie_id: int | None = None
    tmdb_id: int | None = None
    series_id: int | None = None
    season_id: int | None = None


@dataclass(frozen=True, slots=True)
class PosterSourceInput:
    """Server-owned candidate source: confined fixture files or live TMDB."""

    mode: str = "tmdb"  # "tmdb" | "fixture"


@dataclass(slots=True)
class PosterPipelineOutput:
    run_id: str
    status: str
    counts: dict[str, int] = field(default_factory=dict)
    payload: dict[str, object] = field(default_factory=dict)
    recommendation: dict[str, object] | None = None
    ranked: list[dict[str, object]] = field(default_factory=list)
    source_count: int = 0
    candidate_count: int = 0
    scorer_name: str | None = None
    personalization_mode: str = "personalized"
    message: str | None = None


def _movie_of(subject: PosterSubjectInput) -> Movie:
    """A transient (session-less) Movie the stage functions read identity from."""
    return Movie(id=subject.movie_id, title=subject.title, tmdb_id=subject.tmdb_id)


def _fixture_fetch(out_dir: Path) -> tuple[
    dict[str, PosterCandidate],
    dict[str, CandidateScore],
    dict[str, tuple[int, int]],
    list[Path],
]:
    """Build the fetch outputs from confined fixture images staged in the workspace."""
    source_dir = out_dir / _FIXTURE_SUBDIR
    staged = _root_images(source_dir) if source_dir.is_dir() else []
    candidate_map: dict[str, PosterCandidate] = {}
    records: dict[str, CandidateScore] = {}
    resolution_by_name: dict[str, tuple[int, int]] = {}
    all_files: list[Path] = []
    for image in staged:
        destination = out_dir / image.name
        if not destination.exists():
            destination.write_bytes(image.read_bytes())
        with Image.open(destination) as handle:
            width, height = handle.size
        candidate = PosterCandidate(
            file_path=f"/{image.name}",
            width=width,
            height=height,
            aspect_ratio=(width / height if height else 0.0),
            language=None,
            vote_average=0.0,
            vote_count=0,
        )
        candidate_map[image.name] = candidate
        records[image.name] = CandidateScore(
            image_path=destination, orig_filename=image.name, stage_reached="fetch"
        )
        resolution_by_name[image.name] = (width, height)
        all_files.append(destination)
    return candidate_map, records, resolution_by_name, sorted(all_files)


async def run_poster_pipeline(
    *,
    subject: PosterSubjectInput,
    source: PosterSourceInput,
    out_dir: Path,
    feature_extractor: FeatureExtractor,
    ocr_gate: OcrGateContext | None = None,
    progress: ProgressCallback | None = None,
    should_cancel: ShouldCancel | None = None,
    run_id: str | None = None,
    residual_path: Path | None = None,
    residual_context: ResidualRuntimeContext | None = None,
    personalization_mode: str = "personalized",
) -> PosterPipelineOutput:
    """Run the real pipeline for one subject inside ``out_dir`` and summarize it."""
    if personalization_mode not in {"collecting", "personalized"}:
        raise ValueError(f"Unsupported personalization mode: {personalization_mode}")
    run_id = run_id or uuid.uuid4().hex
    movie = _movie_of(subject)
    started_at = datetime.now(UTC).isoformat()
    timings: dict[str, float] = {}
    primary_name: str | None = None
    message = (
        "Marquee filtered unusable posters, but has not learned your preferences yet."
        if personalization_mode == "collecting"
        else None
    )

    if source.mode == "fixture":
        candidate_map, records, resolution_by_name, all_files = _fixture_fetch(out_dir)
        fetch_counts = {
            "posters_found": len(candidate_map),
            "downloaded": 0,
            "skipped": len(all_files),
            "errors": 0,
            "metadata_gated": 0,
        }
    else:
        async with TMDBClient() as tmdb:
            fetch = await fetch_and_download(
                tmdb=tmdb,
                movie=movie,
                originals_dir=out_dir,
                timings=timings,
                progress=progress,
            )
        candidate_map = fetch.candidate_map
        records = fetch.records
        resolution_by_name = fetch.resolution_by_name
        all_files = fetch.all_files
        primary_name = fetch.primary_name
        fetch_counts = fetch.counts

    source_count = len(candidate_map)
    if not all_files:
        payload = build_run_payload(
            movie=movie,
            started_at=started_at,
            status="flagged_manual",
            timings=timings,
            records=records,
            total_duration=0.0,
            run_id=run_id,
            media_type=subject.media_type,
        )
        payload.update(
            personalization_mode=personalization_mode,
            recommendation=None,
            scorer=None,
            message=message,
        )
        return PosterPipelineOutput(
            run_id=run_id,
            status="flagged_manual",
            counts={**fetch_counts, "ranked": 0},
            payload=payload,
            source_count=source_count,
            candidate_count=source_count,
            personalization_mode=personalization_mode,
            message=message,
        )

    sync_started = datetime.now(UTC)
    sync = run_sync_stages(
        movie_title=subject.title,
        out_dir=out_dir,
        records=records,
        candidate_map=candidate_map,
        all_files=all_files,
        resolution_by_name=resolution_by_name,
        timings=timings,
        feature_extractor=feature_extractor,
        primary_name=primary_name,
        progress=progress,
        should_cancel=should_cancel,
        ocr_gate=ocr_gate,
        residual_path=residual_path,
        residual_context=residual_context,
        personalization_mode=personalization_mode,
    )

    if sync.ranked and personalization_mode == "personalized":
        await place_outputs(
            sync.ranked,
            candidate_map=candidate_map,
            out_dir=out_dir,
            timings=timings,
            progress=progress,
        )

    total_duration = (datetime.now(UTC) - sync_started).total_seconds()
    payload = build_run_payload(
        movie=movie,
        started_at=started_at,
        status=sync.status,
        timings=timings,
        records=records,
        total_duration=total_duration,
        run_id=run_id,
        media_type=subject.media_type,
        review_survivors=(sync.ranked if personalization_mode == "collecting" else []),
    )
    counts = {**fetch_counts, **sync.counts}
    recommendation = (
        _recommendation(sync.ranked) if personalization_mode == "personalized" else None
    )
    payload["personalization_mode"] = personalization_mode
    payload["recommendation"] = recommendation
    payload["scorer"] = None if personalization_mode == "collecting" else "weighted"
    payload["message"] = message
    ranked_summary = [
        {
            "rank": record.rank,
            "orig_filename": record.orig_filename,
            "final_score": record.final_score,
            "stack_label": record.stack_label,
        }
        for record in sync.ranked
    ]
    return PosterPipelineOutput(
        run_id=run_id,
        status=sync.status,
        counts=counts,
        payload=payload,
        recommendation=recommendation,
        ranked=ranked_summary,
        source_count=source_count,
        candidate_count=source_count,
        scorer_name=(
            select_scorer(artifact_path=residual_path, context=residual_context).name
            if sync.ranked and personalization_mode == "personalized"
            else None
        ),
        personalization_mode=personalization_mode,
        message=message,
    )


def _recommendation(ranked: list[CandidateScore]) -> dict[str, object] | None:
    if not ranked:
        return None
    top = ranked[0]
    return {
        "orig_filename": top.orig_filename,
        "rank": top.rank,
        "final_score": top.final_score,
        "stack_label": top.stack_label,
    }
