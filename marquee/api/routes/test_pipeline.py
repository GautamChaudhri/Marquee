"""Inspectable movie-only endpoint for the revised poster pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
from dataclasses import replace
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

_EXPERIMENTS_DATA = Path(__file__).resolve().parents[3] / "experiments" / "ocr-first"
_DOWNLOAD_SEMAPHORE = asyncio.Semaphore(5)
_DOWNLOAD_SIZE = "w500"
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
_GENERATED_DIR_NAMES = {
    "sha256",
    "sha256_rejected",
    "phash",
    "phash_rejected",
    "ocr",
    "ocr_rejected",
    "gated",
    "ranked",
    "ranked_lower",
    "errored",
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


@router.post("/pipeline/movie/{movie_id}")
async def test_pipeline_movie(
    movie_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    tmdb: Annotated[TMDBClient, Depends(get_tmdb)],
):
    """Run Fetch -> Dedup -> OCR -> Features -> Gate -> Rank -> Output."""
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
                image_path=out_dir / filename,
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
                        out_dir / _candidate_filename(candidate),
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

        cached_root_files = {path.name: path for path in _root_images(out_dir)}
        all_files = sorted(
            cached_root_files[filename]
            for filename in candidate_map
            if filename in cached_root_files
        )
        if not all_files:
            raise RuntimeError("No poster files were downloaded or found in the cache")
        _stage_done("fetch", stage_started, timings, survivors=len(all_files))

        # Stage 2a: exact SHA-256 dedup.
        stage_started = time.perf_counter()
        logger.info("STAGE START | sha256 | input=%d", len(all_files))
        sha_dir = out_dir / "sha256"
        sha_rejected_dir = sha_dir / "sha256_rejected"
        sha_dir.mkdir(parents=True)
        sha_rejected_dir.mkdir()
        sha_result = PosterDeduper(
            sha256_only=True,
            resolution_by_name=resolution_by_name,
        ).deduplicate(all_files)
        sha_survivor_names = {path.name for path in sha_result.survivors}
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
            destination = sha_dir / path.name
            shutil.copy2(path, destination)
            records[path.name].image_path = destination
            records[path.name].stage_reached = "dedup"
        _stage_done("sha256", stage_started, timings, survivors=len(sha_survivor_names))

        # Stage 2b: perceptual dedup.
        stage_started = time.perf_counter()
        sha_files = sorted(sha_dir.glob("*.*"))
        logger.info("STAGE START | phash | input=%d", len(sha_files))
        phash_dir = sha_dir / "phash"
        phash_rejected_dir = phash_dir / "phash_rejected"
        phash_dir.mkdir()
        phash_rejected_dir.mkdir()
        phash_result = PosterDeduper(
            min_width=0,
            resolution_by_name=resolution_by_name,
        ).deduplicate(sha_files)
        for removal in phash_result.removals:
            if removal.reason != "phash":
                continue
            _log_dedup_removal(removal)
            record = records[removal.removed.name]
            record.stage_reached = "dedup"
            record.rejection_reason = "dedup_phash"
            record.image_path = _copy_with_reason(
                removal.removed,
                phash_rejected_dir,
                "phash",
            )
        for path in phash_result.survivors:
            destination = phash_dir / path.name
            shutil.copy2(path, destination)
            records[path.name].image_path = destination
        _stage_done("phash", stage_started, timings, survivors=phash_result.final)

        # Stage 3: OCR text gate and geometry emission.
        stage_started = time.perf_counter()
        phash_files = sorted(phash_dir.glob("*.*"))
        logger.info("STAGE START | ocr | input=%d", len(phash_files))
        ocr_dir = phash_dir / "ocr"
        ocr_rejected_dir = ocr_dir / "ocr_rejected"
        errored_dir = ocr_dir / "errored"
        ocr_dir.mkdir()
        ocr_rejected_dir.mkdir()
        errored_dir.mkdir()
        ocr_results = PosterTextFilter(movie.title, director=None).filter_batch(
            phash_files
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
            destination = ocr_dir / result.image_path.name
            shutil.copy2(result.image_path, destination)
            record.image_path = destination
            ocr_survivors.append(replace(result, image_path=destination))
        _stage_done("ocr", stage_started, timings, survivors=len(ocr_survivors))

        # Stage 4: feature extraction. Preflight failures are systemic.
        stage_started = time.perf_counter()
        logger.info("STAGE START | features | input=%d", len(ocr_survivors))
        feature_extractor = FeatureExtractor()
        feature_extractor.preflight()
        diagnostic_scorer = WeightedScorer()
        featured: list[CandidateScore] = []
        for ocr_result in ocr_survivors:
            filename = ocr_result.image_path.name
            record = records[filename]
            candidate = candidate_map[filename]
            record.stage_reached = "features"
            try:
                record.features = feature_extractor.extract(ocr_result, candidate)
            except Exception as exc:
                record.rejection_reason = f"feature_error: {exc}"
                record.image_path = _copy_with_reason(
                    ocr_result.image_path,
                    errored_dir,
                    "feature_error",
                )
                logger.exception(
                    "FEATURE ERROR | file=%s | error=%s",
                    filename,
                    exc,
                )
                continue
            featured.append(record)
            _, record.contributions = diagnostic_scorer.score(record.features)
            logger.info(
                "FEATURES | file=%s | raw=%s",
                filename,
                json.dumps(record.features.raw_values(), sort_keys=True),
            )
        _stage_done("features", stage_started, timings, survivors=len(featured))

        # Stage 5: hard global gates.
        stage_started = time.perf_counter()
        logger.info("STAGE START | gate | input=%d", len(featured))
        gate = PosterGate()
        gated: list[CandidateScore] = []
        passed: list[CandidateScore] = []
        for record in featured:
            candidate = candidate_map[record.orig_filename]
            decision = gate.evaluate(
                record.features,
                original_width=candidate.width,
            )
            record.stage_reached = "gate"
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
        gated_dir = ocr_dir / "gated"
        place_gated(gated, gated_dir)
        _stage_done("gate", stage_started, timings, survivors=len(passed))

        status = "completed"
        ranked: list[CandidateScore] = []
        output_result = None
        if not passed:
            status = "flagged_manual"
            logger.warning("RUN FLAGGED | all candidates removed before ranking")
        else:
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
            _stage_done("rank", stage_started, timings, survivors=len(ranked))

            # Stage 7: inspectable output and best-effort original downloads.
            stage_started = time.perf_counter()
            logger.info("STAGE START | output | input=%d", len(ranked))
            output_result = await place_ranked(
                ranked,
                candidate_map=candidate_map,
                ranked_dir=ocr_dir / "ranked",
                lower_dir=ocr_dir / "ranked_lower",
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
            "counts": {
                "sha256_survivors": sha_result.final,
                "phash_survivors": phash_result.final,
                "ocr_survivors": len(ocr_survivors),
                "feature_survivors": len(featured),
                "gated": len(gated),
                "ranked": len(ranked),
            },
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
