"""Inspectable movie-only endpoint for the revised poster pipeline.

Stage order (cheapest signal first — see design 04 §2):

  FETCH -> SHA-256 -> GATE:resolution (TMDB metadata)
        -> STYLE FEATURES (batched CLIP: knn_sim, aesthetic, metadata scalars)
        -> GATE:style (aesthetic floor + rescue, off-style floor)
        -> OCR (text gate + title/residual geometry, style survivors only)
        -> pHash -> DETAIL FEATURES (face, colorfulness, sharpness, residual)
        -> GATE:fan-junk -> RANK -> OUTPUT

Running the embedding gates before OCR means the expensive multi-pass OCR
only sees candidates that are already on-style and above the quality floor —
on a typical movie that cuts OCR volume by a third or more.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.api.deps import get_tmdb
from marquee.core.pipeline_config import pipeline_settings
from marquee.core.poster_sources.tmdb import PosterCandidate, TMDBClient
from marquee.database import get_db
from marquee.models import Movie
from marquee.pipeline.deduper import DedupRemoval, PosterDeduper
from marquee.pipeline.features import FeatureExtractor
from marquee.pipeline.gate import PosterGate
from marquee.pipeline.ocr_filter import PosterTextFilter
from marquee.pipeline.output import place_gated, place_ranked
from marquee.pipeline.scorer import WeightedScorer
from marquee.pipeline.types import CandidateScore, OCRCandidateResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/test", tags=["test"])

_EXPERIMENTS_DATA = Path(__file__).resolve().parents[2] / "experiments" / "runs"
_DOWNLOAD_SEMAPHORE = asyncio.Semaphore(5)
_DOWNLOAD_SIZE = "w500"
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
_GENERATED_DIR_NAMES = {
    # Current flat reject/output structure
    "1-sha256-rejected",
    "2-ocr-rejected",
    "3-phash-rejected",
    "gated",
    "ranked",
    "errored",
    # Legacy names (cleanup of prior runs with old stage ordering)
    "2-phash-rejected",
    "3-ocr-rejected",
    # Older legacy nested names
    "sha256",
    "phash",
    "ocr",
    "sha256_rejected",
    "phash_rejected",
    "ocr_rejected",
    "ranked_lower",
    "clip",
    "clip_rejected",
}


def _sanitise_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


def _candidate_filename(candidate: PosterCandidate) -> str:
    return candidate.file_path.lstrip("/").split("/")[-1]


def _clear_generated_outputs(out_dir: Path) -> None:
    """Remove stale stage artifacts while retaining flat w500 downloads."""
    for child in out_dir.iterdir():
        if child.is_dir() and child.name in _GENERATED_DIR_NAMES:
            shutil.rmtree(child)
        elif child.is_file() and child.name in {"pipeline.log", "pipeline_run.json"}:
            child.unlink()


def _add_run_file_handler(log_path: Path) -> logging.FileHandler:
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        )
    )
    logging.getLogger("marquee").addHandler(handler)
    return handler


def _remove_run_file_handler(handler: logging.FileHandler) -> None:
    logging.getLogger("marquee").removeHandler(handler)
    handler.close()


def _root_images(out_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in out_dir.iterdir()
        if path.is_file() and path.suffix.lower() in _IMAGE_EXTENSIONS
    )


def _copy_with_reason(path: Path, destination: Path, reason: str) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    safe_reason = re.sub(r"[^a-z0-9_]+", "_", reason.lower()).strip("_")
    output = destination / f"{safe_reason}__{path.name}"
    shutil.copy2(path, output)
    return output


def _stage_done(
    name: str,
    started: float,
    timings: dict[str, float],
    *,
    survivors: int,
) -> None:
    elapsed = time.perf_counter() - started
    timings[name] = round(elapsed, 3)
    logger.info(
        "STAGE END | %s | survivors=%d | elapsed=%.3fs",
        name,
        survivors,
        elapsed,
    )


def _log_dedup_removal(removal: DedupRemoval) -> None:
    logger.info(
        "DEDUP REMOVE | file=%s | reason=%s | kept=%s | removed_hash=%s | "
        "kept_hash=%s | distance=%s",
        removal.removed.name,
        removal.reason,
        removal.kept.name if removal.kept else None,
        removal.removed_hash,
        removal.kept_hash,
        removal.distance,
    )


def _write_run_json(
    path: Path,
    *,
    movie: Movie,
    started_at: str,
    status: str,
    timings: dict[str, float],
    records: dict[str, CandidateScore],
    total_duration: float,
    error: str | None = None,
) -> None:
    payload = {
        "movie_id": movie.id,
        "title": movie.title,
        "tmdb_id": movie.tmdb_id,
        "started_at": started_at,
        "completed_at": datetime.now(UTC).isoformat(),
        "status": status,
        "error": error,
        "model_name": pipeline_settings.AI_MODEL,
        "config": pipeline_settings.snapshot(),
        "stage_timings_seconds": timings,
        "total_duration_seconds": round(total_duration, 3),
        "candidates": [
            records[name].to_dict()
            for name in sorted(records)
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


async def _download_poster(
    poster: PosterCandidate,
    destination: Path,
    client: httpx.AsyncClient,
) -> tuple[str, str | None]:
    if destination.exists():
        return "skipped", None
    try:
        async with _DOWNLOAD_SEMAPHORE:
            response = await client.get(poster.url(size=_DOWNLOAD_SIZE))
            response.raise_for_status()
            destination.write_bytes(response.content)
        return "downloaded", None
    except Exception as exc:
        return "error", str(exc)


@dataclass
class _SyncOutcome:
    """Everything the CPU/GPU-bound stages produce, handed back to the async edge."""

    status: str = "completed"
    ranked: list[CandidateScore] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


def _run_sync_stages(
    *,
    movie_title: str,
    out_dir: Path,
    records: dict[str, CandidateScore],
    candidate_map: dict[str, PosterCandidate],
    all_files: list[Path],
    resolution_by_name: dict[str, tuple[int, int]],
    timings: dict[str, float],
) -> _SyncOutcome:
    """All CPU/GPU-bound stages, run off the event loop via asyncio.to_thread."""
    outcome = _SyncOutcome()
    gate = PosterGate()
    gated: list[CandidateScore] = []
    errored_dir = out_dir / "errored"
    errored_dir.mkdir(exist_ok=True)

    # Stage 2a: exact SHA-256 dedup.
    stage_started = time.perf_counter()
    logger.info("STAGE START | sha256 | input=%d", len(all_files))
    sha_rejected_dir = out_dir / "1-sha256-rejected"
    sha_rejected_dir.mkdir()
    sha_result = PosterDeduper(
        sha256_only=True,
        resolution_by_name=resolution_by_name,
    ).deduplicate(all_files)
    for removal in sha_result.removals:
        _log_dedup_removal(removal)
        record = records[removal.removed.name]
        record.stage_reached = "dedup"
        record.rejection_reason = f"dedup_{removal.reason}"
        record.image_path = _copy_with_reason(
            removal.removed,
            sha_rejected_dir,
            removal.reason,
        )
    for path in sha_result.survivors:
        records[path.name].image_path = path
        records[path.name].stage_reached = "dedup"
    outcome.counts["sha256_survivors"] = sha_result.final
    _stage_done("sha256", stage_started, timings, survivors=sha_result.final)

    # Gate 1: resolution floor — pure TMDB metadata, so it runs before any
    # OCR or model inference is spent on candidates that can never pass.
    stage_started = time.perf_counter()
    logger.info("STAGE START | gate-resolution | input=%d", sha_result.final)
    resolution_survivors: list[Path] = []
    for path in sorted(sha_result.survivors):
        record = records[path.name]
        candidate = candidate_map[path.name]
        decision = gate.evaluate_metadata(original_width=candidate.width)
        if decision.passed:
            resolution_survivors.append(path)
            continue
        record.stage_reached = "gate"
        record.gate_decision = "gated"
        record.gate_reason = decision.reason
        record.rejection_reason = decision.reason
        gated.append(record)
        logger.info(
            "GATE REJECT | file=%s | reason=%s | detail=%s",
            path.name,
            decision.reason,
            decision.detail,
        )
    outcome.counts["resolution_gated"] = len(gated)
    _stage_done(
        "gate-resolution",
        stage_started,
        timings,
        survivors=len(resolution_survivors),
    )

    # Stage 4a: style features (batched CLIP) — embedding-driven scalars only.
    # Preflight failures are systemic and abort the run.
    stage_started = time.perf_counter()
    logger.info("STAGE START | style-features | input=%d", len(resolution_survivors))
    feature_extractor = FeatureExtractor()
    feature_extractor.preflight()
    style_items = [
        (path, candidate_map[path.name]) for path in resolution_survivors
    ]
    style_results = feature_extractor.extract_style_batch(style_items)
    styled: list[Path] = []
    for (path, _candidate), result in zip(style_items, style_results, strict=True):
        record = records[path.name]
        record.stage_reached = "features"
        if isinstance(result, Exception):
            record.rejection_reason = f"feature_error: {result}"
            record.image_path = _copy_with_reason(path, errored_dir, "feature_error")
            logger.error("FEATURE ERROR | file=%s | error=%s", path.name, result)
            continue
        record.features = result
        styled.append(path)
        logger.info(
            "STYLE FEATURES | file=%s | knn_sim=%.4f | aesthetic=%.4f",
            path.name,
            result.knn_sim,
            result.aesthetic,
        )
    _stage_done("style-features", stage_started, timings, survivors=len(styled))

    # Gate 2: style gates (aesthetic floor with rescue, off-style floor) —
    # removes off-style/junk candidates BEFORE the expensive OCR stage.
    stage_started = time.perf_counter()
    logger.info("STAGE START | gate-style | input=%d", len(styled))
    style_survivors: list[Path] = []
    style_gated = 0
    for path in styled:
        record = records[path.name]
        decision = gate.evaluate_style(record.features)
        if decision.passed:
            style_survivors.append(path)
            continue
        record.stage_reached = "gate"
        record.gate_decision = "gated"
        record.gate_reason = decision.reason
        record.rejection_reason = decision.reason
        gated.append(record)
        style_gated += 1
        logger.info(
            "GATE REJECT | file=%s | reason=%s | detail=%s",
            path.name,
            decision.reason,
            decision.detail,
        )
    outcome.counts["style_gated"] = style_gated
    _stage_done("gate-style", stage_started, timings, survivors=len(style_survivors))

    # Stage 3: OCR text gate and geometry emission — style survivors only.
    stage_started = time.perf_counter()
    logger.info("STAGE START | ocr | input=%d", len(style_survivors))
    ocr_rejected_dir = out_dir / "2-ocr-rejected"
    ocr_rejected_dir.mkdir()
    ocr_results = PosterTextFilter(movie_title, director=None).filter_batch(
        style_survivors
    )
    ocr_survivors: list[OCRCandidateResult] = []
    for result in ocr_results:
        record = records[result.image_path.name]
        record.stage_reached = "ocr"
        logger.info(
            "OCR | file=%s | accepted=%s | reason=%s | text=%r | "
            "title_bbox=%s | residual_boxes=%d",
            result.image_path.name,
            result.accepted,
            result.reason,
            result.detected_text,
            result.title_bbox,
            len(result.residual_boxes),
        )
        if not result.accepted:
            reason = result.reason or "ocr_rejected"
            record.rejection_reason = reason
            destination_dir = (
                errored_dir if reason.startswith("ocr_error") else ocr_rejected_dir
            )
            record.image_path = _copy_with_reason(
                result.image_path,
                destination_dir,
                reason.split(":", 1)[0],
            )
            continue
        record.image_path = result.image_path
        ocr_survivors.append(replace(result, image_path=result.image_path))
    outcome.counts["ocr_survivors"] = len(ocr_survivors)
    _stage_done("ocr", stage_started, timings, survivors=len(ocr_survivors))

    # Stage 2b: perceptual dedup on OCR survivors.
    # Placed after OCR so text-variant near-duplicates are resolved by the OCR
    # gate rather than a resolution tiebreak (see design §9 pHash placement).
    stage_started = time.perf_counter()
    ocr_survivor_paths = [r.image_path for r in ocr_survivors]
    logger.info("STAGE START | phash | input=%d", len(ocr_survivor_paths))
    phash_rejected_dir = out_dir / "3-phash-rejected"
    phash_rejected_dir.mkdir()
    phash_result = PosterDeduper(
        min_width=0,
        resolution_by_name=resolution_by_name,
    ).deduplicate(ocr_survivor_paths)
    phash_survivor_names = {path.name for path in phash_result.survivors}
    for removal in phash_result.removals:
        if removal.reason != "phash":
            continue
        _log_dedup_removal(removal)
        record = records[removal.removed.name]
        record.stage_reached = "phash"
        record.rejection_reason = "dedup_phash"
        record.image_path = _copy_with_reason(
            removal.removed,
            phash_rejected_dir,
            "phash",
        )
    ocr_survivors = [
        r for r in ocr_survivors if r.image_path.name in phash_survivor_names
    ]
    outcome.counts["phash_survivors"] = len(ocr_survivors)
    _stage_done("phash", stage_started, timings, survivors=len(ocr_survivors))

    # Stage 4b: detail features (face, title colorfulness, sharpness,
    # text_residual) — survivors only, then the remaining hard gate.
    stage_started = time.perf_counter()
    logger.info("STAGE START | detail-features | input=%d", len(ocr_survivors))
    diagnostic_scorer = WeightedScorer()
    passed: list[CandidateScore] = []
    for ocr_result in ocr_survivors:
        filename = ocr_result.image_path.name
        record = records[filename]
        record.stage_reached = "features"
        try:
            record.features = feature_extractor.complete(record.features, ocr_result)
        except Exception as exc:
            record.rejection_reason = f"feature_error: {exc}"
            record.image_path = _copy_with_reason(
                ocr_result.image_path,
                errored_dir,
                "feature_error",
            )
            logger.exception("FEATURE ERROR | file=%s | error=%s", filename, exc)
            continue
        _, record.contributions = diagnostic_scorer.score(record.features)
        logger.info(
            "FEATURES | file=%s | raw=%s",
            filename,
            json.dumps(record.features.raw_values(), sort_keys=True),
        )

        record.stage_reached = "gate"
        decision = gate.evaluate_detail(record.features)
        record.gate_decision = "passed" if decision.passed else "gated"
        record.gate_reason = decision.reason
        if decision.passed:
            passed.append(record)
            logger.info("GATE PASS | file=%s", record.orig_filename)
        else:
            record.rejection_reason = decision.reason
            gated.append(record)
            logger.info(
                "GATE REJECT | file=%s | reason=%s | detail=%s",
                record.orig_filename,
                decision.reason,
                decision.detail,
            )
    outcome.counts["feature_survivors"] = len(passed)
    _stage_done("detail-features", stage_started, timings, survivors=len(passed))

    gated_dir = out_dir / "gated"
    place_gated(gated, gated_dir)
    outcome.counts["gated"] = len(gated)

    if not passed:
        outcome.status = "flagged_manual"
        logger.warning("RUN FLAGGED | all candidates removed before ranking")
        return outcome

    # Stage 6: within-movie ranking.
    stage_started = time.perf_counter()
    logger.info("STAGE START | rank | input=%d", len(passed))
    ranked = diagnostic_scorer.rank(passed)
    total_weight = sum(
        weight
        for weight in pipeline_settings.scorer_weights.values()
        if weight > 0
    )
    for record in ranked:
        logger.info(
            "RANK | rank=%d | file=%s | final_score=%.4f",
            record.rank,
            record.orig_filename,
            record.final_score,
        )
        for feature_name, raw_value in record.features.raw_values().items():
            normalized = record.features.normalized[feature_name]
            configured_weight = pipeline_settings.scorer_weights[feature_name]
            effective_weight = (
                configured_weight / total_weight
                if configured_weight > 0
                else 0.0
            )
            logger.info(
                "RANK DETAIL | file=%s | feature=%s | raw=%.6f | "
                "normalized=%.6f | weight=%.6f | contribution=%.6f",
                record.orig_filename,
                feature_name,
                raw_value,
                normalized,
                effective_weight,
                record.contributions.get(feature_name, 0.0),
            )
    outcome.ranked = ranked
    outcome.counts["ranked"] = len(ranked)
    _stage_done("rank", stage_started, timings, survivors=len(ranked))
    return outcome


@router.post("/pipeline/movie/{movie_id}")
async def test_pipeline_movie(
    movie_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    tmdb: Annotated[TMDBClient, Depends(get_tmdb)],
):
    """Run Fetch -> SHA -> Gate(res) -> Style features -> Gate(style) -> OCR -> pHash -> Detail features -> Gate -> Rank -> Output."""
    movie = (
        await db.execute(select(Movie).where(Movie.id == movie_id))
    ).scalar_one_or_none()
    if movie is None:
        movies = (await db.execute(select(Movie).order_by(Movie.id))).scalars().all()
        if not movies:
            raise HTTPException(
                status_code=400,
                detail="No movies in database - run POST /api/sync/all first",
            )
        raise HTTPException(
            status_code=404,
            detail=(
                f"Movie id={movie_id} not found. Database has {len(movies)} movies "
                f"(id range: {movies[0].id}-{movies[-1].id})."
            ),
        )
    if movie.tmdb_id is None:
        raise HTTPException(
            status_code=400,
            detail=f"Movie {movie.title!r} has no TMDB ID - run sync first",
        )

    run_started = time.perf_counter()
    started_at = datetime.now(UTC).isoformat()
    out_dir = _EXPERIMENTS_DATA / _sanitise_filename(movie.title)
    out_dir.mkdir(parents=True, exist_ok=True)
    _clear_generated_outputs(out_dir)
    originals_dir = out_dir / "0-originals"
    originals_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "pipeline.log"
    json_path = out_dir / "pipeline_run.json"
    file_handler = _add_run_file_handler(log_path)

    timings: dict[str, float] = {}
    records: dict[str, CandidateScore] = {}
    candidate_map: dict[str, PosterCandidate] = {}

    try:
        logger.info("=" * 80)
        logger.info(
            "RUN START | movie=%s | movie_id=%d | tmdb_id=%d | timestamp=%s",
            movie.title,
            movie.id,
            movie.tmdb_id,
            started_at,
        )
        logger.info("CONFIG | %s", json.dumps(pipeline_settings.snapshot(), sort_keys=True))

        # Stage 1: fetch and download.
        stage_started = time.perf_counter()
        logger.info("STAGE START | fetch")
        candidates = await tmdb.get_movie_images(movie.tmdb_id)
        for candidate in candidates:
            filename = _candidate_filename(candidate)
            candidate_map[filename] = candidate
            records[filename] = CandidateScore(
                image_path=originals_dir / filename,
                orig_filename=filename,
                stage_reached="fetch",
            )
        resolution_by_name = {
            filename: (candidate.width, candidate.height)
            for filename, candidate in candidate_map.items()
        }

        downloaded = 0
        skipped = 0
        download_errors = 0
        async with httpx.AsyncClient(timeout=60.0) as client:
            results = await asyncio.gather(
                *[
                    _download_poster(
                        candidate,
                        originals_dir / _candidate_filename(candidate),
                        client,
                    )
                    for candidate in candidates
                ]
            )
        for candidate, (status, error) in zip(candidates, results, strict=True):
            filename = _candidate_filename(candidate)
            if status == "downloaded":
                downloaded += 1
            elif status == "skipped":
                skipped += 1
            else:
                download_errors += 1
                records[filename].rejection_reason = f"download_error: {error}"
                logger.error("FETCH ERROR | file=%s | error=%s", filename, error)

        cached_root_files = {path.name: path for path in _root_images(originals_dir)}
        all_files = sorted(
            cached_root_files[filename]
            for filename in candidate_map
            if filename in cached_root_files
        )
        if not all_files:
            raise RuntimeError("No poster files were downloaded or found in the cache")
        _stage_done("fetch", stage_started, timings, survivors=len(all_files))

        # Stages 2-6 are CPU/GPU-bound: run them off the event loop so the
        # API (health checks, other requests) stays responsive.
        outcome = await asyncio.to_thread(
            _run_sync_stages,
            movie_title=movie.title,
            out_dir=out_dir,
            records=records,
            candidate_map=candidate_map,
            all_files=all_files,
            resolution_by_name=resolution_by_name,
            timings=timings,
        )
        status = outcome.status
        ranked = outcome.ranked

        # Stage 7: inspectable output and best-effort original downloads.
        output_result = None
        if ranked:
            stage_started = time.perf_counter()
            logger.info("STAGE START | output | input=%d", len(ranked))
            output_result = await place_ranked(
                ranked,
                candidate_map=candidate_map,
                ranked_dir=out_dir / "ranked",
            )
            for record in ranked[:5]:
                logger.info(
                    "OUTPUT TOP | rank=%d | file=%s | score=%.4f | "
                    "original_download=%s | path=%s",
                    record.rank,
                    record.orig_filename,
                    record.final_score,
                    record.original_download,
                    record.image_path,
                )
            _stage_done("output", stage_started, timings, survivors=len(ranked))

        total_duration = time.perf_counter() - run_started
        _write_run_json(
            json_path,
            movie=movie,
            started_at=started_at,
            status=status,
            timings=timings,
            records=records,
            total_duration=total_duration,
        )

        logger.info(
            "RUN SUMMARY | status=%s | top5=%s | total=%.3fs | timings=%s",
            status,
            [
                {
                    "rank": record.rank,
                    "file": record.orig_filename,
                    "score": record.final_score,
                    "original_download": record.original_download,
                }
                for record in ranked[:5]
            ],
            total_duration,
            timings,
        )
        logger.info("RUN END | output=%s", out_dir)
        logger.info("=" * 80)

        return {
            "movie_id": movie.id,
            "title": movie.title,
            "tmdb_id": movie.tmdb_id,
            "status": status,
            "output_dir": str(out_dir),
            "pipeline_log": str(log_path),
            "pipeline_run_json": str(json_path),
            "total_duration_s": round(total_duration, 3),
            "stage_timings_s": timings,
            "fetch": {
                "posters_found": len(candidates),
                "downloaded": downloaded,
                "skipped": skipped,
                "errors": download_errors,
            },
            "counts": outcome.counts,
            "top5": [
                {
                    "rank": record.rank,
                    "score": record.final_score,
                    "original_file": record.orig_filename,
                    "output_file": record.image_path.name,
                    "original_download": record.original_download,
                }
                for record in ranked[:5]
            ],
            "original_download_errors": (
                output_result.download_errors if output_result else []
            ),
        }
    except HTTPException:
        raise
    except Exception as exc:
        total_duration = time.perf_counter() - run_started
        logger.exception("RUN FAILED | systemic_error=%s", exc)
        _write_run_json(
            json_path,
            movie=movie,
            started_at=started_at,
            status="failed",
            timings=timings,
            records=records,
            total_duration=total_duration,
            error=str(exc),
        )
        raise HTTPException(
            status_code=500,
            detail=f"Pipeline infrastructure failure: {exc}",
        ) from exc
    finally:
        _remove_run_file_handler(file_handler)
