"""Pipeline execution core — the stages, decoupled from any HTTP route.

This module owns the actual poster-selection work that used to live inline in
``api/routes/test_pipeline.py``. It is import-light (no FastAPI) so it can be
driven by the production ``RunManager`` (with live progress + a process-
lifetime feature extractor) and by the legacy test endpoint alike.

Two changes vs the original inline version:

  - Every stage boundary fires an optional ``progress`` callback (start/end,
    plus per-poster ticks inside the long OCR stage), so the API can stream
    live progress over SSE.
  - The expensive ``FeatureExtractor`` is injected, not constructed per run —
    the ``RunManager`` keeps one for the process lifetime, which fixes the
    per-run ONNX-session VRAM growth (see design 09 §14).

Stage order (cheapest signal first — see design 04 §2):

  FETCH (metadata gate pre-download) -> SHA-256 -> GATE:resolution (safety net)
        -> STYLE FEATURES (batched CLIP: knn_sim, aesthetic, metadata scalars)
        -> GATE:style (aesthetic floor + rescue, off-style floor)
        -> OCR (text gate + title/residual geometry, style survivors only)
        -> pHash -> DETAIL FEATURES (face, colorfulness, sharpness, residual)
        -> GATE:fan-junk -> RANK -> OUTPUT
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import numpy as np

from marquee.config import settings
from marquee.core.cancellation import JobCancelledError
from marquee.core.download_guard import ensure_image_response
from marquee.core.pipeline_config import pipeline_settings
from marquee.core.poster_sources.tmdb import PosterCandidate, TMDBClient
from marquee.core.text_profiles import OcrGateContext
from marquee.models import Movie
from marquee.pipeline.deduper import DedupRemoval, PosterDeduper
from marquee.pipeline.features import FeatureExtractor, load_cached_embedding
from marquee.pipeline.gate import PosterGate
from marquee.pipeline.ocr_filter import PosterTextFilter
from marquee.pipeline.output import OutputResult, place_gated, place_ranked
from marquee.pipeline.scorer import (
    ResidualCompatibilityError,
    ResidualRuntimeContext,
    WeightedScorer,
    select_scorer,
)
from marquee.pipeline.stacker import assign_stacks
from marquee.pipeline.types import (
    BoundingBox,
    CandidateScore,
    OCRCandidateResult,
    OCRTextBox,
)

logger = logging.getLogger(__name__)

_RUNS_WORK_DATA = settings.runs_work_path
_EXPERIMENTS_DATA = _RUNS_WORK_DATA
_DOWNLOAD_SEMAPHORE = asyncio.Semaphore(5)
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


# ---------------------------------------------------------------------------
# Progress events (consumed by RunManager → SSE)
# ---------------------------------------------------------------------------


@dataclass
class ProgressEvent:
    """A single live progress beat for the SSE stream.

    The ``movie_*`` and ``batch_*`` fields are populated only by the cross-movie
    batch runner so a UI can show "stage X, movie 3/12 (Dune)". They stay None
    for single-movie runs, keeping the event backward compatible.
    """

    stage: str
    state: str  # "start" | "progress" | "end"
    done: int | None = None
    total: int | None = None
    survivors: int | None = None
    elapsed_s: float | None = None
    # Batch context (None for single-movie runs).
    movie_id: int | None = None
    title: str | None = None
    movie_index: int | None = None  # 1-based position within the batch
    movie_total: int | None = None
    movies_done: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "state": self.state,
            "done": self.done,
            "total": self.total,
            "survivors": self.survivors,
            "elapsed_s": self.elapsed_s,
            "movie_id": self.movie_id,
            "title": self.title,
            "movie_index": self.movie_index,
            "movie_total": self.movie_total,
            "movies_done": self.movies_done,
        }


ProgressCallback = Callable[[ProgressEvent], None]
ShouldCancel = Callable[[], bool]


def _emit(progress: ProgressCallback | None, event: ProgressEvent) -> None:
    if progress is not None:
        progress(event)


# ---------------------------------------------------------------------------
# Filesystem + logging helpers
# ---------------------------------------------------------------------------


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
        logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
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


def _ocr_bbox_to_list(bbox: BoundingBox | None) -> list | None:
    """A title bbox (4 corner points) as JSON-native [[x, y], ...]."""
    if bbox is None:
        return None
    return [[float(x), float(y)] for x, y in bbox]


def _ocr_box_to_dict(box: OCRTextBox) -> dict:
    """An OCR text box as a JSON-native dict for the per-run archive."""
    return {
        "text": box.text,
        "confidence": float(box.confidence),
        "bbox": _ocr_bbox_to_list(box.bbox),
        "area": float(box.area),
        "geometry_valid": bool(box.geometry_valid),
    }


def _attach_ocr_diagnostics(record: CandidateScore, result: OCRCandidateResult) -> None:
    """Persist what OCR actually read onto the record so the diagnostics live in
    the immutable per-run archive instead of only the volatile pipeline.log.

    Used by the ``run_sync_stages`` engine so the OCR diagnostics live in the
    immutable per-run archive, not only the volatile pipeline.log.

    The full structured trace (``result.diagnostics``) is only persisted on
    DEBUG runs — it is several KB per poster and only the OCR-label tooling
    (a DEBUG-gated dev surface) consumes it, so production archives stay lean.
    """
    record.ocr_detected_text = result.detected_text
    record.ocr_title_bbox = _ocr_bbox_to_list(result.title_bbox)
    record.ocr_residual_boxes = [_ocr_box_to_dict(box) for box in result.residual_boxes]
    record.ocr_trace = result.diagnostics if settings.DEBUG else None


def _stage_done(
    name: str,
    started: float,
    timings: dict[str, float],
    *,
    survivors: int,
    progress: ProgressCallback | None = None,
) -> None:
    elapsed = time.perf_counter() - started
    timings[name] = round(elapsed, 3)
    logger.info("STAGE END | %s | survivors=%d | elapsed=%.3fs", name, survivors, elapsed)
    _emit(
        progress,
        ProgressEvent(
            stage=name,
            state="end",
            survivors=survivors,
            elapsed_s=round(elapsed, 3),
        ),
    )


def _stage_start(
    name: str,
    *,
    total: int | None = None,
    progress: ProgressCallback | None = None,
) -> float:
    if total is None:
        logger.info("STAGE START | %s", name)
    else:
        logger.info("STAGE START | %s | input=%d", name, total)
    _emit(progress, ProgressEvent(stage=name, state="start", total=total))
    return time.perf_counter()


def _log_detail_features(
    feature_extractor: FeatureExtractor,
    record: CandidateScore,
) -> None:
    """Per-candidate decision trace: every measured value, every typicality."""
    features = record.features
    raw = {
        name: (round(value, 6) if value is not None else None)
        for name, value in features.raw_values().items()
    }
    logger.info(
        "FEATURES | file=%s | raw=%s",
        record.orig_filename,
        json.dumps(raw, sort_keys=True),
    )
    if features.extended:
        logger.info(
            "FEATURES EXTENDED | file=%s | %s",
            record.orig_filename,
            json.dumps(
                {k: round(v, 6) for k, v in features.extended.items()},
                sort_keys=True,
            ),
        )
    calibration = getattr(feature_extractor, "_calibration", None)
    if features.typicality_detail and calibration is not None:
        entries = []
        for name in sorted(features.typicality_detail):
            value = features.aesthetic if name == "aesthetic" else features.extended.get(name)
            band = calibration.band(name)
            band_text = (
                f"[p10={band.p10:.3f} med={band.median:.3f} p90={band.p90:.3f}]" if band else "[?]"
            )
            entries.append(
                f"{name}={value:.4f}->{features.typicality_detail[name]:.3f} {band_text}"
            )
        logger.info(
            "TYPICALITY | file=%s | mean=%s | %s",
            record.orig_filename,
            (
                f"{features.taste_typicality:.4f}"
                if features.taste_typicality is not None
                else "n/a"
            ),
            " | ".join(entries),
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


def build_run_payload(
    *,
    movie: Movie,
    started_at: str,
    status: str,
    timings: dict[str, float],
    records: dict[str, CandidateScore],
    total_duration: float,
    run_id: str | None = None,
    error: str | None = None,
    media_type: str = "movie",
    subject: dict[str, object] | None = None,
    review_survivors: list[CandidateScore] | None = None,
) -> dict[str, object]:
    """The full ``pipeline_run.json`` payload (also archived per run_id).

    ``media_type``/``subject`` are additive TV fields (design 04 §9.2) — movie
    callers keep the default, so the payload shape only gains the new
    ``media_type: "movie"`` key.
    """
    from marquee.ml.taste_store import compute_taste_profile_hash  # noqa: PLC0415

    diagnostics = [records[name].to_dict() for name in sorted(records)]
    survivors = review_survivors or []
    ordered_survivors: list[dict[str, object]] = []
    for position, record in enumerate(survivors):
        if (
            record.gate_decision != "passed"
            or record.rejection_reason is not None
            or record.features is None
        ):
            raise ValueError("review survivors must pass every objective gate")
        reference = record.orig_filename
        opaque_id = hashlib.sha256(
            f"jmc7b-review-v1{chr(0)}{run_id or ''}{chr(0)}{reference}".encode()
        ).hexdigest()
        ordered_survivors.append(
            {
                "candidate_id": opaque_id,
                "reference": reference,
                "position": position,
                "objective_eligible": True,
            }
        )

    payload: dict[str, object] = {
        "run_id": run_id,
        "movie_id": movie.id,
        "title": movie.title,
        "tmdb_id": movie.tmdb_id,
        "started_at": started_at,
        "completed_at": datetime.now(UTC).isoformat(),
        "status": status,
        "error": error,
        "model_name": pipeline_settings.AI_MODEL,
        "taste_profile_hash": compute_taste_profile_hash(),  # For reproducibility
        "config": pipeline_settings.snapshot(),
        "stage_timings_seconds": timings,
        "total_duration_seconds": round(total_duration, 3),
        "diagnostic_ledger": {
            "version": 1,
            "candidates": diagnostics,
        },
        "review": {
            "version": 1,
            "order_algorithm": "source_family_round_robin_sha256_v1",
            "survivors": ordered_survivors,
            "eligible_count": len(ordered_survivors),
            "archived_count": 0,
            "truncated_count": 0,
        },
        "media_type": media_type,
    }
    if subject is not None:
        payload["subject"] = subject
    return payload


def _json_default(obj: object) -> object:
    """Coerce the numpy scalars/arrays the ML feature extras emit (calibration
    typicality, zero-shot axes, etc.) into JSON-native values so every run
    archives instead of silently failing to serialize. Keeps numeric fidelity
    (float/int, not str) because the archive is replayed for rescoring."""
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        value = float(obj)
        return value if math.isfinite(value) else None
    if isinstance(obj, np.ndarray):
        return _strip_nonfinite(obj.tolist())
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _strip_nonfinite(obj: object) -> object:
    """Replace NaN/±Infinity with None throughout a JSON-bound structure.

    Archives are echoed back through Starlette responses, which render with
    ``allow_nan=False`` — a single NaN feature (e.g. title geometry with no
    title box) would 500 every endpoint that replays the archive.
    """
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {key: _strip_nonfinite(value) for key, value in obj.items()}
    if isinstance(obj, list | tuple):
        return [_strip_nonfinite(item) for item in obj]
    return obj


def write_run_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(_strip_nonfinite(payload), indent=2, default=_json_default),
        encoding="utf-8",
    )


async def _download_poster(
    poster: PosterCandidate,
    destination: Path,
    client: httpx.AsyncClient,
) -> tuple[str, str | None]:
    if destination.exists():
        return "skipped", None
    try:
        async with _DOWNLOAD_SEMAPHORE:
            response = await client.get(poster.url(size=pipeline_settings.TMDB_POSTER_SIZE))
            response.raise_for_status()
            ensure_image_response(response)
            destination.write_bytes(response.content)
        return "downloaded", None
    except Exception as exc:
        return "error", str(exc)


# ---------------------------------------------------------------------------
# Fetch (async edge)
# ---------------------------------------------------------------------------


@dataclass
class FetchOutcome:
    candidate_map: dict[str, PosterCandidate]
    records: dict[str, CandidateScore]
    resolution_by_name: dict[str, tuple[int, int]]
    all_files: list[Path]
    primary_name: str | None
    counts: dict[str, int]


async def fetch_candidates(
    tmdb: TMDBClient,
    movie: Movie,
    *,
    media_type: str = "movie",
    season_number: int | None = None,
) -> tuple[list[PosterCandidate], str | None]:
    """Fetch TMDB poster metadata for one subject — no downloads.

    ``media_type`` picks the TMDB namespace. A series id sent to ``/movie`` is
    not merely a 404: ids are allocated per namespace, so a series id that also
    exists as a movie id returns that unrelated movie's posters.

    Returns ``(candidates, primary_name)``.  The caller is responsible for
    applying metadata gates and downloading the survivors.
    """
    if media_type == "season":
        if season_number is None:
            raise ValueError("a season poster run requires the season number")
        candidates = await tmdb.get_season_images(movie.tmdb_id, season_number)
    elif media_type == "series":
        candidates = await tmdb.get_tv_images(movie.tmdb_id)
    elif media_type == "movie":
        candidates = await tmdb.get_movie_images(movie.tmdb_id)
    else:
        raise ValueError(f"unsupported poster media type: {media_type!r}")

    try:
        if media_type == "season":
            primary_name = await tmdb.get_season_primary_poster(movie.tmdb_id, season_number)
        elif media_type == "series":
            primary_name = await tmdb.get_tv_primary_poster(movie.tmdb_id)
        else:
            primary_name = await tmdb.get_movie_primary_poster(movie.tmdb_id)
    except Exception as exc:
        primary_name = None
        logger.warning(
            "FETCH | primary poster lookup failed (%s) — official_family disabled this run",
            exc,
        )
    logger.info("FETCH | media_type=%s primary_poster=%s", media_type, primary_name)
    return candidates, primary_name


async def fetch_and_download(
    *,
    tmdb: TMDBClient,
    movie: Movie,
    originals_dir: Path,
    timings: dict[str, float],
    progress: ProgressCallback | None = None,
    media_type: str = "movie",
    season_number: int | None = None,
) -> FetchOutcome:
    """Stage 1: fetch candidate metadata, gate by resolution (no download),
    then download every survivor at the configured size."""
    stage_started = _stage_start("fetch", progress=progress)
    candidates, primary_name = await fetch_candidates(
        tmdb, movie, media_type=media_type, season_number=season_number
    )

    # Build candidate_map, records, and resolution_by_name for ALL
    # candidates (gated candidates stay in the map so downstream stages
    # and run archives see the full candidate set).
    candidate_map: dict[str, PosterCandidate] = {}
    records: dict[str, CandidateScore] = {}
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

    # ── Metadata gates (resolution floor + future metadata-only gates) ───
    # Run BEFORE any download so bandwidth/disk-rejected posters are never
    # fetched from the CDN (design 18 §7).
    gate = PosterGate()
    downloadable: list[PosterCandidate] = []
    metadata_gated = 0
    for candidate in candidates:
        decision = gate.evaluate_metadata(original_width=candidate.width)
        if decision.passed:
            downloadable.append(candidate)
        else:
            filename = _candidate_filename(candidate)
            records[filename].rejection_reason = "resolution_floor"
            records[filename].gate_decision = "gated"
            metadata_gated += 1
    if metadata_gated:
        logger.info(
            "FETCH | metadata_gated=%d (of %d total candidates)",
            metadata_gated,
            len(candidates),
        )

    downloaded = skipped = download_errors = 0
    async with httpx.AsyncClient(timeout=60.0) as client:
        results = await asyncio.gather(
            *[
                _download_poster(
                    candidate,
                    originals_dir / _candidate_filename(candidate),
                    client,
                )
                for candidate in downloadable
            ]
        )
    for candidate, (status, error) in zip(downloadable, results, strict=True):
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
        cached_root_files[filename] for filename in candidate_map if filename in cached_root_files
    )
    if not all_files:
        raise RuntimeError("No poster files were downloaded or found in the cache")
    _stage_done("fetch", stage_started, timings, survivors=len(all_files), progress=progress)

    return FetchOutcome(
        candidate_map=candidate_map,
        records=records,
        resolution_by_name=resolution_by_name,
        all_files=all_files,
        primary_name=primary_name,
        counts={
            "posters_found": len(candidates),
            "downloaded": downloaded,
            "skipped": skipped,
            "errors": download_errors,
            "metadata_gated": metadata_gated,
        },
    )


# ---------------------------------------------------------------------------
# Synchronous CPU/GPU stages
# ---------------------------------------------------------------------------


@dataclass
class SyncOutcome:
    """Everything the CPU/GPU-bound stages produce, handed back to the async edge."""

    status: str = "completed"
    ranked: list[CandidateScore] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


def _neutral_candidate_order(
    records: list[CandidateScore],
    *,
    movie_title: str,
    candidate_map: dict[str, PosterCandidate],
) -> list[CandidateScore]:
    """Stable, source-diverse ordering with no quality or taste score."""
    groups: dict[str, list[CandidateScore]] = {}
    for record in records:
        candidate = candidate_map[record.orig_filename]
        source_family = candidate.language or "language-neutral"
        groups.setdefault(source_family, []).append(record)
    for source_family, members in groups.items():
        members.sort(
            key=lambda record: hashlib.sha256(
                f"{movie_title}\0{source_family}\0{record.orig_filename}".encode()
            ).digest()
        )
    ordered: list[CandidateScore] = []
    families = sorted(groups)
    while any(groups.values()):
        for family in families:
            if groups[family]:
                ordered.append(groups[family].pop(0))
    for rank, record in enumerate(ordered, start=1):
        record.rank = rank
        record.final_score = None
        record.contributions = {}
    return ordered


def _stack_signal_value(record: CandidateScore, image_path: Path, dino_vector):
    """The per-poster similarity value the stacker groups on (STACK_SIGNAL).

    dino → the DINOv2 vector retained from detail features (CLIP cache as a
    fallback when DINO is off); clip → the cached CLIP embedding; phash → a
    perceptual hash. Returns None when the signal can't be computed, so the
    stacker leaves that poster as its own singleton.
    """
    signal = pipeline_settings.STACK_SIGNAL
    if signal == "phash":
        import imagehash  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415

        try:
            with Image.open(image_path) as img:
                return imagehash.phash(img)
        except Exception:  # noqa: BLE001 — missing hash → singleton stack
            logger.warning("STACK | pHash failed for %s — singleton", image_path.name)
            return None
    if signal == "clip":
        return load_cached_embedding(record.orig_filename)
    # dino (default): use the retained vector, fall back to the CLIP cache
    # when DINO produced nothing for this poster (model off / per-item error).
    if dino_vector is not None:
        return dino_vector
    return load_cached_embedding(record.orig_filename)


def run_sync_stages(
    *,
    movie_title: str,
    out_dir: Path,
    records: dict[str, CandidateScore],
    candidate_map: dict[str, PosterCandidate],
    all_files: list[Path],
    resolution_by_name: dict[str, tuple[int, int]],
    timings: dict[str, float],
    feature_extractor: FeatureExtractor,
    primary_name: str | None = None,
    progress: ProgressCallback | None = None,
    should_cancel: ShouldCancel | None = None,
    ocr_gate: OcrGateContext | None = None,
    residual_path: Path | None = None,
    residual_context: ResidualRuntimeContext | None = None,
    personalization_mode: str = "personalized",
) -> SyncOutcome:
    """All CPU/GPU-bound stages, run off the event loop via asyncio.to_thread."""

    def check_cancelled() -> None:
        if should_cancel is not None and should_cancel():
            raise JobCancelledError("poster pipeline cancelled")

    outcome = SyncOutcome()
    gate = PosterGate()
    gated: list[CandidateScore] = []
    errored_dir = out_dir / "errored"
    errored_dir.mkdir(exist_ok=True)
    check_cancelled()

    # Stage 2a: exact SHA-256 dedup.
    stage_started = _stage_start("sha256", total=len(all_files), progress=progress)
    sha_rejected_dir = out_dir / "1-sha256-rejected"
    sha_rejected_dir.mkdir()
    sha_result = PosterDeduper(
        sha256_only=True,
        resolution_by_name=resolution_by_name,
    ).deduplicate(all_files)
    check_cancelled()
    for removal in sha_result.removals:
        _log_dedup_removal(removal)
        record = records[removal.removed.name]
        record.stage_reached = "dedup"
        record.rejection_reason = f"dedup_{removal.reason}"
        record.dedup_kept = removal.kept.name if removal.kept else None
        record.image_path = _copy_with_reason(
            removal.removed,
            sha_rejected_dir,
            removal.reason,
        )
    for path in sha_result.survivors:
        records[path.name].image_path = path
        records[path.name].stage_reached = "dedup"
    outcome.counts["sha256_survivors"] = sha_result.final
    _stage_done("sha256", stage_started, timings, survivors=sha_result.final, progress=progress)

    # Gate 1: resolution floor — pure TMDB metadata, before any inference.
    # As of design 18 §7, metadata gates run pre-download in fetch_and_download,
    # so this loop is a safety net — it should never fire under normal operation.
    stage_started = _stage_start("gate-resolution", total=sha_result.final, progress=progress)
    resolution_survivors: list[Path] = []
    for path in sorted(sha_result.survivors):
        check_cancelled()
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
        progress=progress,
    )

    # Stage 4a: style features (batched CLIP). Preflight is the extractor's
    # responsibility (done once by the RunManager); systemic failures raise.
    stage_started = _stage_start(
        "style-features", total=len(resolution_survivors), progress=progress
    )
    style_items = [(path, candidate_map[path.name]) for path in resolution_survivors]
    check_cancelled()
    style_results = feature_extractor.extract_style_batch(style_items, primary_name=primary_name)
    styled: list[Path] = []
    for (path, _candidate), result in zip(style_items, style_results, strict=True):
        check_cancelled()
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
            "STYLE FEATURES | file=%s | knn_sim=%.4f | aesthetic=%.4f | axes=%s",
            path.name,
            result.knn_sim,
            result.aesthetic,
            json.dumps(
                {k: round(v, 4) for k, v in result.extended.items()},
                sort_keys=True,
            ),
        )
    _stage_done("style-features", stage_started, timings, survivors=len(styled), progress=progress)

    # Gate 2: style gates (aesthetic floor + rescue, off-style floor).
    stage_started = _stage_start("gate-style", total=len(styled), progress=progress)
    style_survivors: list[Path] = []
    style_gated = 0
    for path in styled:
        check_cancelled()
        record = records[path.name]
        decision = gate.evaluate_style(
            record.features, personalization_mode=personalization_mode
        )
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
    _stage_done(
        "gate-style", stage_started, timings, survivors=len(style_survivors), progress=progress
    )

    # Stage 3: OCR text gate and geometry emission — style survivors only.
    stage_started = _stage_start("ocr", total=len(style_survivors), progress=progress)
    ocr_rejected_dir = out_dir / "2-ocr-rejected"
    ocr_rejected_dir.mkdir()

    def _ocr_tick(done: int, total: int) -> None:
        check_cancelled()
        _emit(progress, ProgressEvent(stage="ocr", state="progress", done=done, total=total))

    check_cancelled()
    # Per-movie gate context (director tokens + effective text profile); the
    # direct/test path without one falls back to the global default profile.
    gate_ctx = ocr_gate or OcrGateContext.default()
    ocr_results = PosterTextFilter(
        movie_title,
        director=gate_ctx.director,
        studios=gate_ctx.studios,
        tagline=gate_ctx.tagline,
        profile=gate_ctx.profile,
    ).filter_batch(style_survivors, progress=_ocr_tick)
    ocr_survivors: list[OCRCandidateResult] = []
    for result in ocr_results:
        check_cancelled()
        record = records[result.image_path.name]
        record.stage_reached = "ocr"
        # Durably capture what OCR actually read — for every candidate that
        # reached this stage, accepted or rejected — so the diagnostics live in
        # the immutable per-run archive instead of only the volatile log.
        _attach_ocr_diagnostics(record, result)
        logger.info(
            "OCR | file=%s | accepted=%s | reason=%s | text=%r | title_bbox=%s | residual_boxes=%d",
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
            destination_dir = errored_dir if reason.startswith("ocr_error") else ocr_rejected_dir
            record.image_path = _copy_with_reason(
                result.image_path,
                destination_dir,
                reason.split(":", 1)[0],
            )
            continue
        record.image_path = result.image_path
        ocr_survivors.append(replace(result, image_path=result.image_path))
    outcome.counts["ocr_survivors"] = len(ocr_survivors)
    _stage_done("ocr", stage_started, timings, survivors=len(ocr_survivors), progress=progress)

    # Stage 2b: perceptual dedup on OCR survivors. When STACK_ENABLED this
    # removal stage is replaced by the stack layer (same-design variants are
    # grouped and ranked, not deleted), so it is skipped entirely.
    if pipeline_settings.STACK_ENABLED and personalization_mode == "personalized":
        outcome.counts["phash_survivors"] = len(ocr_survivors)
    else:
        stage_started = _stage_start("phash", total=len(ocr_survivors), progress=progress)
        ocr_survivor_paths = [r.image_path for r in ocr_survivors]
        phash_rejected_dir = out_dir / "3-phash-rejected"
        phash_rejected_dir.mkdir()
        dedup_preference = {
            r.image_path.name: (
                1 if r.title_bbox is not None else 0,
                -len(r.residual_boxes),
                1 if r.image_path.name == primary_name else 0,
                (
                    records[r.image_path.name].features.knn_sim
                    if personalization_mode == "personalized"
                    else 0.0
                ),
            )
            for r in ocr_survivors
            if records[r.image_path.name].features is not None
        }
        phash_result = PosterDeduper(
            min_width=0,
            resolution_by_name=resolution_by_name,
            preference_by_name=dedup_preference,
        ).deduplicate(ocr_survivor_paths)
        check_cancelled()
        phash_survivor_names = {path.name for path in phash_result.survivors}
        for removal in phash_result.removals:
            if removal.reason != "phash":
                continue
            _log_dedup_removal(removal)
            record = records[removal.removed.name]
            record.stage_reached = "phash"
            record.rejection_reason = "dedup_phash"
            record.dedup_kept = removal.kept.name if removal.kept else None
            record.image_path = _copy_with_reason(
                removal.removed,
                phash_rejected_dir,
                "phash",
            )
        ocr_survivors = [r for r in ocr_survivors if r.image_path.name in phash_survivor_names]
        outcome.counts["phash_survivors"] = len(ocr_survivors)
        _stage_done(
            "phash", stage_started, timings, survivors=len(ocr_survivors), progress=progress
        )

    # Stage 4b: detail features + the remaining hard gate.
    stage_started = _stage_start("detail-features", total=len(ocr_survivors), progress=progress)
    diagnostic_scorer = (
        select_scorer(artifact_path=residual_path, context=residual_context)
        if personalization_mode == "personalized"
        else None
    )
    passed: list[CandidateScore] = []
    detail_items = [(records[r.image_path.name].features, r) for r in ocr_survivors]
    # The stacker reuses the DINOv2 vectors computed here as its grouping
    # signal — capture them keyed by item index (aligned to ocr_survivors).
    dino_vectors: dict = {}
    check_cancelled()
    detail_results = feature_extractor.complete_batch(detail_items, dino_vectors_out=dino_vectors)
    for index, (ocr_result, detail_result) in enumerate(
        zip(ocr_survivors, detail_results, strict=True)
    ):
        check_cancelled()
        filename = ocr_result.image_path.name
        record = records[filename]
        record.stage_reached = "features"
        if isinstance(detail_result, Exception):
            record.rejection_reason = f"feature_error: {detail_result}"
            record.image_path = _copy_with_reason(
                ocr_result.image_path,
                errored_dir,
                "feature_error",
            )
            logger.error("FEATURE ERROR | file=%s | error=%s", filename, detail_result)
            continue
        record.features = detail_result
        if diagnostic_scorer is not None:
            try:
                _, record.contributions = diagnostic_scorer.score(record.features)
            except RuntimeError as exc:
                if pipeline_settings.SCORER == "residual":
                    raise ResidualCompatibilityError(
                        f"forced residual cannot score this feature schema: {exc}"
                    ) from exc
                logger.warning("SCORER | weighted (auto: residual dormant: %s)", exc)
                diagnostic_scorer = WeightedScorer()
                _, record.contributions = diagnostic_scorer.score(record.features)
        _log_detail_features(feature_extractor, record)

        record.stage_reached = "gate"
        decision = gate.evaluate_detail(record.features)
        record.gate_decision = "passed" if decision.passed else "gated"
        record.gate_reason = decision.reason
        if decision.passed:
            if pipeline_settings.STACK_ENABLED and personalization_mode == "personalized":
                record.embedding = _stack_signal_value(
                    record, ocr_result.image_path, dino_vectors.get(index)
                )
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
    _stage_done("detail-features", stage_started, timings, survivors=len(passed), progress=progress)

    gated_dir = out_dir / "gated"
    place_gated(gated, gated_dir)
    outcome.counts["gated"] = len(gated)

    if not passed:
        outcome.status = "flagged_manual"
        logger.warning("RUN FLAGGED | all candidates removed before ranking")
        return outcome

    if personalization_mode == "collecting":
        stage_started = _stage_start("neutral-order", total=len(passed), progress=progress)
        outcome.ranked = _neutral_candidate_order(
            passed, movie_title=movie_title, candidate_map=candidate_map
        )
        outcome.counts["ranked"] = len(outcome.ranked)
        _stage_done(
            "neutral-order",
            stage_started,
            timings,
            survivors=len(outcome.ranked),
            progress=progress,
        )
        return outcome

    # Stage 6: within-movie ranking.
    stage_started = _stage_start("rank", total=len(passed), progress=progress)
    assert diagnostic_scorer is not None
    logger.info("RANK | scorer=%s", diagnostic_scorer.name)
    check_cancelled()
    ranked = diagnostic_scorer.rank(passed)
    # Stage 6b: group same-design variants into stacks and rank designs
    # against each other (auto-pick = 1A). Mutates stack fields on records;
    # the flat `ranked` order (by global rank) is kept for output/back-compat.
    if pipeline_settings.STACK_ENABLED:
        assign_stacks(ranked)
    for record in ranked:
        check_cancelled()
        logger.info(
            "RANK | rank=%d | file=%s | final_score=%.4f | scorer=%s",
            record.rank,
            record.orig_filename,
            record.final_score,
            diagnostic_scorer.name,
        )
        configured_weights = pipeline_settings.scorer_weights
        active_total = sum(
            weight
            for name, weight in configured_weights.items()
            if weight > 0 and name in record.features.normalized
        )
        for feature_name, raw_value in record.features.raw_values().items():
            normalized = record.features.normalized.get(feature_name)
            if normalized is None:
                logger.info(
                    "RANK DETAIL | file=%s | feature=%s | absent (not computed "
                    "this run — weight redistributed)",
                    record.orig_filename,
                    feature_name,
                )
                continue
            configured_weight = configured_weights.get(feature_name, 0.0)
            effective_weight = (
                configured_weight / active_total
                if configured_weight > 0 and active_total > 0
                else 0.0
            )
            logger.info(
                "RANK DETAIL | file=%s | feature=%s | raw=%.6f | "
                "normalized=%.6f | weight=%.6f | contribution=%.6f",
                record.orig_filename,
                feature_name,
                raw_value if raw_value is not None else float("nan"),
                normalized,
                effective_weight,
                record.contributions.get(feature_name, 0.0),
            )
    outcome.ranked = ranked
    outcome.counts["ranked"] = len(ranked)
    _stage_done("rank", stage_started, timings, survivors=len(ranked), progress=progress)
    return outcome


async def place_outputs(
    ranked: list[CandidateScore],
    *,
    candidate_map: dict[str, PosterCandidate],
    out_dir: Path,
    timings: dict[str, float],
    progress: ProgressCallback | None = None,
) -> OutputResult:
    """Stage 7: ranked placement + best-effort full-resolution re-download."""
    stage_started = _stage_start("output", total=len(ranked), progress=progress)
    result = await place_ranked(
        ranked,
        candidate_map=candidate_map,
        ranked_dir=out_dir / "ranked",
    )
    for record in ranked[:5]:
        logger.info(
            "OUTPUT TOP | rank=%d | file=%s | score=%.4f | original_download=%s | path=%s",
            record.rank,
            record.orig_filename,
            record.final_score,
            record.original_download,
            record.image_path,
        )
    _stage_done("output", stage_started, timings, survivors=len(ranked), progress=progress)
    return result
