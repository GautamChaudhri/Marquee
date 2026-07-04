"""Cross-movie, stage-batched poster pipeline.

The single-movie engine (``runner.run_sync_stages``) runs the whole pipeline for
one movie and spins the PaddleOCR worker pool up and down each time. When a batch
of movies is processed that way, the pool's model load is paid once *per movie*.

This module instead streams **every movie through each stage together**, so the
two costliest shared resources load once for the whole batch:

  * **OCR** — a single ``PosterTextFilter.run_ocr_batch`` over every movie's
    style survivors, with per-task title tokens (the no-text fallback is then
    applied per movie).
  * **DINOv2 detail features** — one ``FeatureExtractor.complete_batch`` over the
    union of all survivors.

Everything else (SHA/pHash dedup, gates, ranking, output, archive) is per movie
and reuses the exact same leaf helpers as the single-movie path, so a candidate's
recorded fields and the run archive are byte-for-byte compatible with
``/api/pipeline/runs/{run_id}`` — each movie still gets its own ``run_id`` +
``PipelineRun`` row, tagged with the batch's job id.

Stage order matches ``runner`` (cheapest signal first); only the *grouping*
across movies changes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import select

from marquee.config import settings
from marquee.core.pipeline_config import PipelineSettings, pipeline_settings
from marquee.core.poster_sources.tmdb import PosterCandidate
from marquee.core.text_profiles import OcrGateContext
from marquee.database import _get_session_factory
from marquee.ml.hardware import effective_ocr_workers
from marquee.ml.namespaces import TasteNamespace, get_namespace
from marquee.models import Movie, PipelineRun
from marquee.pipeline.deduper import PosterDeduper
from marquee.pipeline.features import FeatureExtractor
from marquee.pipeline.gate import PosterGate
from marquee.pipeline.ocr_filter import OcrPool, PosterTextFilter, apply_no_text_fallback
from marquee.pipeline.output import place_gated
from marquee.pipeline.runner import (
    FetchOutcome,
    ProgressEvent,
    _attach_ocr_diagnostics,
    _candidate_filename,
    _clear_generated_outputs,
    _copy_with_reason,
    _log_dedup_removal,
    _root_images,
    _sanitise_filename,
    _stack_signal_value,
    build_run_payload,
    fetch_candidates,
    place_outputs,
    write_run_json,
)
from marquee.pipeline.scorer import select_scorer
from marquee.pipeline.stacker import assign_stacks
from marquee.pipeline.types import (
    CandidateScore,
    OCRCandidateResult,
    find_auto_pick_candidate,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[ProgressEvent], None]
ShouldCancel = Callable[[], bool]

_BATCH_DOWNLOAD_SEMAPHORE = asyncio.Semaphore(15)


# ---------------------------------------------------------------------------
# Asset input contract (movie | series | season) for the generalized batch
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssetSpec:
    media_type: str  # movie | series | season
    subject_id: int  # movie.id / series.id / season.id
    title: str  # display title (PosterSubject.title convention)
    tmdb_id: int | None  # movie tmdb or series tmdb
    series_id: int | None = None
    season_id: int | None = None
    season_number: int | None = None
    ocr_title: str | None = None  # series title (bare, no season suffix) for TV OCR


# ---------------------------------------------------------------------------
# Per-movie context carried through the shared stages
# ---------------------------------------------------------------------------


@dataclass
class _BatchMovie:
    run_id: str
    movie_id: int  # subject_id for TV assets (series.id / season.id)
    title: str
    tmdb_id: int | None
    out_dir: Path
    originals_dir: Path
    started_at: str
    start_perf: float
    index: int  # 1-based position in the batch (for progress)
    total: int
    fetch: FetchOutcome | None = None
    # Per-movie OCR gate context (director tokens + effective text profile).
    ocr_gate: OcrGateContext | None = None
    # media_type/series_id/season_id/season_number/ocr_title are additive TV
    # fields — movie assets keep every default, so movie behavior is unchanged.
    media_type: str = "movie"
    series_id: int | None = None
    season_id: int | None = None
    season_number: int | None = None
    ocr_title: str | None = None  # None => use `title` for OCR tokens (movie default)
    timings: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    status: str = "running"  # coerced to a terminal status at finalize
    error: str | None = None
    # Working survivor sets between shared stages.
    style_survivors: list[Path] = field(default_factory=list)
    ocr_survivors: list[OCRCandidateResult] = field(default_factory=list)
    passed: list[CandidateScore] = field(default_factory=list)
    gated: list[CandidateScore] = field(default_factory=list)
    ranked: list[CandidateScore] = field(default_factory=list)
    official_pick: dict[str, object] | None = None
    # Transient — candidates that passed metadata gates, pending Phase B download.
    _downloadable: list[PosterCandidate] = field(default_factory=list)

    @property
    def records(self) -> dict[str, CandidateScore]:
        assert self.fetch is not None
        return self.fetch.records

    @property
    def duration(self) -> float:
        return time.perf_counter() - self.start_perf


# Generalized name per design/plans/04-television-backend.md §9.2. Kept as an
# alias (not a rename) so existing direct `_BatchMovie(...)` construction in
# tests/test_batch_runner.py keeps working unchanged.
_BatchItem = _BatchMovie


def _run_fk_kwargs(ctx: _BatchMovie) -> dict[str, object]:
    """PipelineRun/ArtworkEvent subject FK columns for this context's asset."""
    if ctx.media_type == "movie":
        return {"media_type": "movie", "movie_id": ctx.movie_id, "series_id": None, "season_id": None}
    elif ctx.media_type == "series":
        return {"media_type": "series", "movie_id": None, "series_id": ctx.series_id, "season_id": None}
    elif ctx.media_type == "season":
        return {
            "media_type": "season",
            "movie_id": None,
            "series_id": ctx.series_id,
            "season_id": ctx.season_id,
        }
    raise ValueError(f"Unknown media_type {ctx.media_type!r}")


def _official_pick_enabled(media_type: str, config: PipelineSettings) -> bool:
    flags = {
        "movie": config.OFFICIAL_PICK_MOVIE,
        "series": config.OFFICIAL_PICK_SHOW,
        "season": config.OFFICIAL_PICK_SEASON,
    }
    try:
        return flags[media_type]
    except KeyError:
        raise ValueError(f"Unknown media_type {media_type!r}") from None


def apply_official_pick(
    ranked: list[CandidateScore],
    primary_name: str | None,
    *,
    enabled: bool,
    fallback: str,
) -> dict[str, object]:
    """Post-stacking promotion: bump the TMDB primary poster's stack to rank 1.

    Call after ``assign_stacks()`` and before the archive/output are written.
    Mutates ``stack_rank`` on every affected member so
    ``find_auto_pick_candidate`` (``stack_rank == 1 and stack_pos == 1``)
    picks up the promotion automatically — no other code path needs to change.
    """
    if not enabled or primary_name is None or not ranked:
        return {"enabled": enabled, "primary_name": primary_name, "applied": None}
    if ranked[0].stack_rank is None:
        # STACK_ENABLED=False — no stack metadata to promote.
        return {
            "enabled": enabled,
            "primary_name": primary_name,
            "applied": None,
            "reason": "stacking_disabled",
        }

    def _promote(target_rank: int) -> None:
        for candidate in ranked:
            if candidate.stack_rank < target_rank:
                candidate.stack_rank += 1
            elif candidate.stack_rank == target_rank:
                candidate.stack_rank = 1

    target = next((c for c in ranked if c.orig_filename == primary_name), None)
    if target is not None:
        if target.stack_rank != 1:
            _promote(target.stack_rank)
        return {"enabled": enabled, "primary_name": primary_name, "applied": "primary_stack"}

    if fallback == "largest_stack":
        stack_sizes: dict[int, int] = {}
        for candidate in ranked:
            stack_sizes.setdefault(candidate.stack_rank, candidate.stack_size or 1)
        largest_rank = min(stack_sizes, key=lambda rank: (-stack_sizes[rank], rank))
        if largest_rank != 1:
            _promote(largest_rank)
        return {
            "enabled": enabled,
            "primary_name": primary_name,
            "applied": "largest_stack",
            "fallback_used": True,
        }

    return {
        "enabled": enabled,
        "primary_name": primary_name,
        "applied": None,
        "fallback_used": True,
    }


async def _download_phase(
    contexts: list[_BatchMovie],
    progress: ProgressCallback | None,
) -> None:
    """Download every metadata-gated candidate across the whole batch under
    one shared semaphore (cross-movie interleaving — design 18 §8), emitting
    a global progress tick per download and a per-movie "fetch end" as soon
    as that movie's own downloads finish.

    Without the global tick, ``job.progress`` freezes for the entire download
    phase: the per-movie "fetch start"/"end" events fire all-at-once at each
    end of the phase (start when metadata gating finishes, end only once
    every movie's downloads are done), so nothing updates in between even
    though real download work is happening for a while.
    """
    download_items: list[tuple[_BatchMovie, PosterCandidate]] = []
    for ctx in contexts:
        if ctx.fetch is None:
            continue
        for candidate in ctx._downloadable:
            download_items.append((ctx, candidate))

    def _finalize_fetch(ctx: _BatchMovie) -> None:
        cached = {path.name: path for path in _root_images(ctx.originals_dir)}
        ctx.fetch.all_files = sorted(cached[fn] for fn in ctx.fetch.candidate_map if fn in cached)
        if not ctx.fetch.all_files:
            ctx.status = "failed"
            ctx.error = "No poster files were downloaded or found in the cache"
        _emit(progress, ctx, "fetch", "end", survivors=len(ctx.fetch.all_files))

    total_downloads = len(download_items)
    downloads_done = 0
    downloads_errored = 0
    _emit_global(progress, "fetch", "start", total=total_downloads)

    if download_items:
        by_movie: dict[int, list[PosterCandidate]] = defaultdict(list)
        ctx_by_key: dict[int, _BatchMovie] = {}
        for ctx, candidate in download_items:
            by_movie[id(ctx)].append(candidate)
            ctx_by_key[id(ctx)] = ctx

        async with httpx.AsyncClient(timeout=60.0) as client:

            async def _dl(ctx: _BatchMovie, candidate: PosterCandidate) -> None:
                nonlocal downloads_done, downloads_errored
                dest = ctx.originals_dir / _candidate_filename(candidate)
                if dest.exists():
                    ctx.fetch.counts["skipped"] += 1
                else:
                    async with _BATCH_DOWNLOAD_SEMAPHORE:
                        try:
                            response = await client.get(
                                candidate.url(size=pipeline_settings.TMDB_POSTER_SIZE)
                            )
                            response.raise_for_status()
                            dest.write_bytes(response.content)
                            ctx.fetch.counts["downloaded"] += 1
                        except Exception as exc:
                            filename = _candidate_filename(candidate)
                            ctx.fetch.counts["errors"] += 1
                            downloads_errored += 1
                            ctx.fetch.records[filename].rejection_reason = f"download_error: {exc}"
                            logger.error(
                                "BATCH DOWNLOAD ERROR | movie=%s | file=%s | %s",
                                ctx.title,
                                filename,
                                exc,
                            )
                downloads_done += 1
                _emit_global(
                    progress, "fetch", "progress", done=downloads_done, total=total_downloads
                )

            async def _download_movie(ctx: _BatchMovie, candidates: list[PosterCandidate]) -> None:
                # Each movie's "fetch end" fires as soon as ITS OWN downloads
                # finish, not after the entire cross-movie pool — every _dl()
                # call still draws from the same shared semaphore, so overall
                # interleaving/throughput across movies is unchanged.
                await asyncio.gather(*[_dl(ctx, c) for c in candidates])
                _finalize_fetch(ctx)

            await asyncio.gather(
                *(_download_movie(ctx_by_key[key], cands) for key, cands in by_movie.items())
            )

    _emit_global(progress, "fetch", "end", survivors=total_downloads - downloads_errored)

    # Movies that failed metadata fetch (Phase A) or had nothing to download
    # never pass through the per-movie download group above — finalize them
    # immediately instead of leaving their "fetch" stage dangling.
    for ctx in contexts:
        if ctx.status == "cancelled":
            continue
        if ctx.fetch is None:
            _emit(progress, ctx, "fetch", "end", survivors=0)
        elif not ctx._downloadable:
            _finalize_fetch(ctx)


# ---------------------------------------------------------------------------
# Public async entry
# ---------------------------------------------------------------------------


async def run_batch(
    *,
    job_id: str,
    movies: list[tuple[int, str, int | None]],
    tmdb,
    extractor: FeatureExtractor,
    progress: ProgressCallback | None = None,
    should_cancel: ShouldCancel | None = None,
    ocr_meta: dict[int, OcrGateContext] | None = None,
) -> dict[str, object]:
    """Run the stage-batched pipeline over ``movies`` (id, title, tmdb_id).

    ``ocr_meta`` maps movie_id → per-movie OCR gate context (director tokens +
    effective text profile). Movies without an entry use the global default
    profile.

    Thin adapter over ``run_batch_assets`` — signature and observable behavior
    are unchanged (guarded by tests/test_batch_runner.py and
    tests/test_poster_pipeline_backend.py).
    """
    assets = [
        AssetSpec(media_type="movie", subject_id=movie_id, title=title, tmdb_id=tmdb_id)
        for movie_id, title, tmdb_id in movies
    ]
    asset_ocr_meta = {("movie", movie_id): ctx for movie_id, ctx in (ocr_meta or {}).items()}
    return await run_batch_assets(
        job_id=job_id,
        assets=assets,
        tmdb=tmdb,
        extractor=extractor,
        taste_namespace=get_namespace("movies"),
        progress=progress,
        should_cancel=should_cancel,
        ocr_meta=asset_ocr_meta,
    )


async def run_batch_assets(
    *,
    job_id: str,
    assets: list[AssetSpec],
    tmdb,
    extractor: FeatureExtractor,
    taste_namespace: TasteNamespace,
    progress: ProgressCallback | None = None,
    should_cancel: ShouldCancel | None = None,
    ocr_meta: dict[tuple[str, int], OcrGateContext] | None = None,
) -> dict[str, object]:
    """Run the stage-batched pipeline over a mix of movie/series/season assets.

    ``ocr_meta`` maps ``(media_type, subject_id)`` → per-asset OCR gate context
    (director tokens + effective text profile). Assets without an entry use
    the global default profile.

    Returns a summary dict (per-status counts + per-asset run ids) for the job
    result. Each asset gets its own archived run + ``PipelineRun`` row tagged
    with ``batch_id=job_id``.
    """
    should_cancel = should_cancel or (lambda: False)
    persisted_running = False
    caught: Exception | None = None
    summary: dict[str, object] | None = None
    scorer_name: str | None = None

    contexts: list[_BatchMovie] = []
    total = len(assets)
    default_gate = OcrGateContext.default()
    for index, asset in enumerate(assets, 1):
        out_dir = settings.runs_work_path / _sanitise_filename(asset.title)
        out_dir.mkdir(parents=True, exist_ok=True)
        _clear_generated_outputs(out_dir)
        originals = out_dir / "0-originals"
        originals.mkdir(parents=True, exist_ok=True)
        contexts.append(
            _BatchMovie(
                run_id=uuid4().hex,
                movie_id=asset.subject_id,
                title=asset.title,
                tmdb_id=asset.tmdb_id,
                out_dir=out_dir,
                originals_dir=originals,
                started_at=datetime.now(UTC).isoformat(),
                start_perf=time.perf_counter(),
                index=index,
                total=total,
                ocr_gate=(ocr_meta or {}).get(
                    (asset.media_type, asset.subject_id), default_gate
                ),
                media_type=asset.media_type,
                series_id=asset.series_id,
                season_id=asset.season_id,
                season_number=asset.season_number,
                ocr_title=asset.ocr_title,
            )
        )

    # ── Phase A: fetch TMDB metadata for ALL assets concurrently ────────
    # (design 18 §8 — metadata calls are fast and independent)
    async def _fetch_meta(ctx: _BatchMovie) -> None:
        if ctx.tmdb_id is None:
            raise RuntimeError("no TMDB ID — run sync first")
        if ctx.media_type == "movie":
            movie = Movie(id=ctx.movie_id, title=ctx.title, tmdb_id=ctx.tmdb_id)
            candidates, primary_name = await fetch_candidates(tmdb, movie)
        elif ctx.media_type == "series":
            candidates = await tmdb.get_tv_images(ctx.tmdb_id)
            try:
                primary_name = await tmdb.get_tv_primary_poster(ctx.tmdb_id)
            except Exception as exc:
                primary_name = None
                logger.warning(
                    "FETCH | series primary poster lookup failed (%s) — official_family disabled",
                    exc,
                )
        elif ctx.media_type == "season":
            candidates = await tmdb.get_season_images(ctx.tmdb_id, ctx.season_number)
            try:
                primary_name = await tmdb.get_season_primary_poster(ctx.tmdb_id, ctx.season_number)
            except Exception as exc:
                primary_name = None
                logger.warning(
                    "FETCH | season primary poster lookup failed (%s) — official_family disabled",
                    exc,
                )
        else:
            raise ValueError(f"Unknown media_type {ctx.media_type!r}")

        # Build candidate_map, records, resolution_by_name for ALL candidates.
        candidate_map: dict[str, PosterCandidate] = {}
        records: dict[str, CandidateScore] = {}
        for candidate in candidates:
            filename = _candidate_filename(candidate)
            candidate_map[filename] = candidate
            records[filename] = CandidateScore(
                image_path=ctx.originals_dir / filename,
                orig_filename=filename,
                stage_reached="fetch",
            )
        resolution_by_name = {
            filename: (candidate.width, candidate.height)
            for filename, candidate in candidate_map.items()
        }

        # Metadata gates (resolution floor) — pre-download (design 18 §7).
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
                "BATCH META | movie=%s | metadata_gated=%d (of %d)",
                ctx.title,
                metadata_gated,
                len(candidates),
            )

        ctx.fetch = FetchOutcome(
            candidate_map=candidate_map,
            records=records,
            resolution_by_name=resolution_by_name,
            all_files=[],  # filled after Phase B downloads
            primary_name=primary_name,
            counts={
                "posters_found": len(candidates),
                "downloaded": 0,
                "skipped": 0,
                "errors": 0,
                "metadata_gated": metadata_gated,
            },
        )
        ctx._downloadable = downloadable

    try:
        await _persist_running(contexts, job_id)
        persisted_running = True
        logger.info("BATCH START | job=%s | movies=%d", job_id, total)

        fetch_tasks = []
        for ctx in contexts:
            if should_cancel():
                ctx.status = "cancelled"
                continue
            _emit(progress, ctx, "fetch", "start")
            fetch_tasks.append(_fetch_meta(ctx))

        fetch_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
        fetchable = [c for c in contexts if c.status != "cancelled"]
        for ctx, result in zip(fetchable, fetch_results, strict=True):
            if isinstance(result, Exception):
                ctx.status = "failed"
                ctx.error = str(result)
                logger.warning("BATCH FETCH FAILED | movie=%s | %s", ctx.title, result)
            if ctx.fetch is not None:
                ctx.counts.update(ctx.fetch.counts)

        # ── Phase B: cross-movie parallel download pool ─────────────────────
        # (design 18 §8 — all surviving posters across all movies interleave
        # under one semaphore so downloads from multiple movies overlap)
        await _download_phase(contexts, progress)

        live = [c for c in contexts if c.fetch is not None]

        # ── Shared CPU/GPU stages off the event loop ─────────────────────────
        if live and not should_cancel():
            await asyncio.to_thread(
                _run_sync_stages, live, extractor, progress, should_cancel, taste_namespace
            )

        # ── OUTPUT (async best-effort full-res re-download) ──────────────────
        for ctx in live:
            if should_cancel():
                break
            if ctx.ranked:
                _emit(progress, ctx, "output", "start", total=len(ctx.ranked))
                await place_outputs(
                    ctx.ranked,
                    candidate_map=ctx.fetch.candidate_map,
                    out_dir=ctx.out_dir,
                    timings=ctx.timings,
                    progress=None,
                )
                _emit(progress, ctx, "output", "end", survivors=len(ctx.ranked))

        scorer_name = select_scorer(pipeline_settings, namespace=taste_namespace).name
    except Exception as exc:
        caught = exc
        logger.exception("BATCH FAILED | job=%s | %s", job_id, exc)
    finally:
        if persisted_running:
            if caught is not None:
                for ctx in contexts:
                    if ctx.status not in _TERMINAL:
                        ctx.error = ctx.error or str(caught)
            summary = await _finalize(
                contexts,
                job_id=job_id,
                scorer_name=scorer_name,
                cancelled=should_cancel(),
            )
            logger.info("BATCH END | job=%s | %s", job_id, summary)

    if caught is not None:
        raise caught
    assert summary is not None
    return summary


# ---------------------------------------------------------------------------
# Synchronous shared stages (run inside asyncio.to_thread)
# ---------------------------------------------------------------------------


def _run_sync_stages(
    contexts: list[_BatchMovie],
    extractor: FeatureExtractor,
    progress: ProgressCallback | None,
    should_cancel: ShouldCancel,
    taste_namespace: TasteNamespace | None = None,
) -> None:
    gate = PosterGate()
    ns = taste_namespace or get_namespace("movies")
    # Idempotent per batch — swaps the taste store + calibration arrays
    # without reloading any ONNX session, so an interleaved worker never
    # scores this batch against the wrong profile.
    extractor.set_taste_namespace(ns)

    # Stage 1 (per movie): SHA-256 dedup → resolution gate → style features → style gate.
    for ctx in contexts:
        if should_cancel():
            ctx.status = "cancelled"
            continue
        _movie_prelude(ctx, gate, extractor, progress)

    if should_cancel():
        return

    # Stage 2 (batched): OCR.  Preload the worker pool now — PaddleOCR model
    # load overlaps with the last movie's style-gate tail.  By the time the
    # first task is queued every worker is already resident.
    ocr_pool = _start_ocr_pool_for(contexts)
    ocr_done = False
    try:
        if should_cancel():
            return

        _ocr_batch(contexts, progress, pool=ocr_pool)

        if should_cancel():
            return

        # Tear down the OCR pool on a background thread so the detail-features
        # stage (DINOv2) can start immediately while worker processes unwind.
        threading.Thread(
            target=PosterTextFilter.stop_ocr_pool,
            args=(ocr_pool,),
            daemon=True,
        ).start()
        ocr_done = True
    except Exception:
        PosterTextFilter.stop_ocr_pool(ocr_pool)
        raise
    finally:
        if not ocr_done:
            PosterTextFilter.stop_ocr_pool(ocr_pool)

    # Stage 3 (per movie): perceptual dedup on each movie's OCR survivors.
    for ctx in contexts:
        _phash(ctx, progress)

    if should_cancel():
        return

    # Stage 4 (batched): detail features (DINOv2) over the union of survivors.
    _detail_batch(contexts, extractor, gate, progress, ns)

    # Stage 5 (per movie): place gated, rank survivors.
    scorer = select_scorer(pipeline_settings, namespace=ns)
    for ctx in contexts:
        _rank(ctx, scorer, progress)


def _movie_prelude(
    ctx: _BatchMovie,
    gate: PosterGate,
    extractor: FeatureExtractor,
    progress: ProgressCallback | None,
) -> None:
    """SHA-256 dedup + resolution gate + style features + style gate (one movie)."""
    records = ctx.records
    candidate_map = ctx.fetch.candidate_map
    resolution_by_name = ctx.fetch.resolution_by_name
    out_dir = ctx.out_dir
    (out_dir / "errored").mkdir(exist_ok=True)

    # SHA-256 exact dedup.
    _emit(progress, ctx, "sha256", "start", total=len(ctx.fetch.all_files))
    sha_dir = out_dir / "1-sha256-rejected"
    sha_dir.mkdir(exist_ok=True)
    sha = PosterDeduper(sha256_only=True, resolution_by_name=resolution_by_name).deduplicate(
        ctx.fetch.all_files
    )
    for removal in sha.removals:
        _log_dedup_removal(removal)
        record = records[removal.removed.name]
        record.stage_reached = "dedup"
        record.rejection_reason = f"dedup_{removal.reason}"
        record.dedup_kept = removal.kept.name if removal.kept else None
        record.image_path = _copy_with_reason(removal.removed, sha_dir, removal.reason)
    for path in sha.survivors:
        records[path.name].image_path = path
        records[path.name].stage_reached = "dedup"
    ctx.counts["sha256_survivors"] = sha.final
    _emit(progress, ctx, "sha256", "end", survivors=sha.final)

    # Resolution gate (TMDB metadata).
    # As of design 18 §7, metadata gates run pre-download in Phase A,
    # so this loop is a safety net — it should never fire under normal operation.
    resolution_survivors: list[Path] = []
    for path in sorted(sha.survivors):
        record = records[path.name]
        decision = gate.evaluate_metadata(original_width=candidate_map[path.name].width)
        if decision.passed:
            resolution_survivors.append(path)
            continue
        record.stage_reached = "gate"
        record.gate_decision = "gated"
        record.gate_reason = decision.reason
        record.rejection_reason = decision.reason
        ctx.gated.append(record)
    ctx.counts["resolution_gated"] = len(ctx.gated)

    # Style features (batched CLIP for this movie; model already resident).
    _emit(progress, ctx, "style-features", "start", total=len(resolution_survivors))
    style_items = [(path, candidate_map[path.name]) for path in resolution_survivors]
    style_results = extractor.extract_style_batch(style_items, primary_name=ctx.fetch.primary_name)
    styled: list[Path] = []
    for (path, _candidate), result in zip(style_items, style_results, strict=True):
        record = records[path.name]
        record.stage_reached = "features"
        if isinstance(result, Exception):
            record.rejection_reason = f"feature_error: {result}"
            record.image_path = _copy_with_reason(path, out_dir / "errored", "feature_error")
            continue
        record.features = result
        styled.append(path)
    _emit(progress, ctx, "style-features", "end", survivors=len(styled))

    # Style gates (aesthetic floor + rescue, off-style floor).
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
        ctx.gated.append(record)
        style_gated += 1
    ctx.counts["style_gated"] = style_gated
    ctx.style_survivors = style_survivors


def _start_ocr_pool_for(contexts: list[_BatchMovie]) -> OcrPool:
    """Count total OCR items across all movies and start the worker pool.

    Called during the prelude phase so PaddleOCR loads in parallel with
    the last few style-feature batches.
    """
    total = sum(len(ctx.style_survivors) for ctx in contexts)
    if total == 0:
        total = 1  # start_ocr_pool requires at least 1 worker
    return PosterTextFilter.start_ocr_pool(num_workers=min(effective_ocr_workers(), total))


def _ocr_batch(
    contexts: list[_BatchMovie],
    progress: ProgressCallback | None,
    *,
    pool=None,
) -> None:
    """Run OCR over every movie's style survivors in ONE worker pool.

    When *pool* (an ``OcrPool`` from ``PosterTextFilter.start_ocr_pool``) is
    passed the workers are assumed already loaded — only the task feed +
    result collection runs.  Otherwise a fresh pool is created and destroyed
    inside the call (backward-compatible single-shot path).
    """
    items: list[tuple[Path, str, set[str], set[str], dict | None]] = []
    owners: list[_BatchMovie] = []
    for ctx in contexts:
        gate_ctx = ctx.ocr_gate
        tokens = PosterTextFilter(  # cheap — no model load
            ctx.ocr_title or ctx.title,
            director=gate_ctx.director if gate_ctx else None,
            studios=gate_ctx.studios if gate_ctx else None,
            tagline=gate_ctx.tagline if gate_ctx else None,
            profile=gate_ctx.profile if gate_ctx else None,
        )
        extras = tokens.task_extras()
        for path in ctx.style_survivors:
            items.append((path, tokens.title, tokens.title_tokens, tokens.director_tokens, extras))
            owners.append(ctx)

    for ctx in contexts:
        (ctx.out_dir / "2-ocr-rejected").mkdir(exist_ok=True)
    if not items:
        for ctx in contexts:
            ctx.counts["ocr_survivors"] = 0
        return

    total = len(items)
    _emit_global(progress, "ocr", "start", total=total)

    def _tick(done: int, total_: int) -> None:
        _emit_global(progress, "ocr", "progress", done=done, total=total_)

    if pool is not None:
        results = PosterTextFilter.run_ocr_tasks(pool, items, progress=_tick)
    else:
        results = PosterTextFilter.run_ocr_batch(items, progress=_tick)

    by_movie: dict[int, list[OCRCandidateResult]] = defaultdict(list)
    for owner, result in zip(owners, results, strict=True):
        by_movie[owner.movie_id].append(result)

    survivors_total = 0
    for ctx in contexts:
        movie_results = apply_no_text_fallback(by_movie.get(ctx.movie_id, []))
        records = ctx.records
        ocr_rejected_dir = ctx.out_dir / "2-ocr-rejected"
        errored_dir = ctx.out_dir / "errored"
        ocr_survivors: list[OCRCandidateResult] = []
        for result in movie_results:
            record = records[result.image_path.name]
            record.stage_reached = "ocr"
            # Persist OCR diagnostics for every candidate (accepted or rejected)
            # — the UI runs through this batch engine, so without this the
            # per-run archive carries null OCR data and label capture falls back
            # to synthesized logs. Shared helper keeps this in lockstep with the
            # single-movie engine in runner.run_sync_stages.
            _attach_ocr_diagnostics(record, result)
            if not result.accepted:
                reason = result.reason or "ocr_rejected"
                record.rejection_reason = reason
                destination = errored_dir if reason.startswith("ocr_error") else ocr_rejected_dir
                record.image_path = _copy_with_reason(
                    result.image_path, destination, reason.split(":", 1)[0]
                )
                continue
            record.image_path = result.image_path
            ocr_survivors.append(replace(result, image_path=result.image_path))
        ctx.ocr_survivors = ocr_survivors
        ctx.counts["ocr_survivors"] = len(ocr_survivors)
        survivors_total += len(ocr_survivors)

    _emit_global(progress, "ocr", "end", survivors=survivors_total)


def _phash(ctx: _BatchMovie, progress: ProgressCallback | None) -> None:
    """Perceptual near-dupe removal on one movie's OCR survivors.

    Skipped when STACK_ENABLED — the stack layer groups same-design variants
    instead of deleting them, so every OCR survivor flows on to detail features.
    """
    if pipeline_settings.STACK_ENABLED:
        ctx.counts["phash_survivors"] = len(ctx.ocr_survivors)
        return
    if not ctx.ocr_survivors:
        ctx.counts["phash_survivors"] = 0
        return
    records = ctx.records
    primary_name = ctx.fetch.primary_name
    phash_dir = ctx.out_dir / "3-phash-rejected"
    phash_dir.mkdir(exist_ok=True)
    survivor_paths = [r.image_path for r in ctx.ocr_survivors]
    preference = {
        r.image_path.name: (
            1 if r.title_bbox is not None else 0,
            -len(r.residual_boxes),
            1 if r.image_path.name == primary_name else 0,
            records[r.image_path.name].features.knn_sim,
        )
        for r in ctx.ocr_survivors
        if records[r.image_path.name].features is not None
    }
    phash = PosterDeduper(
        min_width=0,
        resolution_by_name=ctx.fetch.resolution_by_name,
        preference_by_name=preference,
    ).deduplicate(survivor_paths)
    survivor_names = {path.name for path in phash.survivors}
    for removal in phash.removals:
        if removal.reason != "phash":
            continue
        _log_dedup_removal(removal)
        record = records[removal.removed.name]
        record.stage_reached = "phash"
        record.rejection_reason = "dedup_phash"
        record.dedup_kept = removal.kept.name if removal.kept else None
        record.image_path = _copy_with_reason(removal.removed, phash_dir, "phash")
    ctx.ocr_survivors = [r for r in ctx.ocr_survivors if r.image_path.name in survivor_names]
    ctx.counts["phash_survivors"] = len(ctx.ocr_survivors)


def _detail_batch(
    contexts: list[_BatchMovie],
    extractor: FeatureExtractor,
    gate: PosterGate,
    progress: ProgressCallback | None,
    namespace: TasteNamespace | None = None,
) -> None:
    """Detail features (DINOv2 batched once) over the union of survivors, then
    the detail gate per candidate."""
    items: list[tuple] = []
    owners: list[tuple[_BatchMovie, OCRCandidateResult]] = []
    for ctx in contexts:
        records = ctx.records
        for r in ctx.ocr_survivors:
            items.append((records[r.image_path.name].features, r))
            owners.append((ctx, r))

    if not items:
        return

    _emit_global(progress, "detail-features", "start", total=len(items))
    # The stacker reuses these DINOv2 vectors as its grouping signal (keyed
    # by index into the union `items`/`owners`).
    dino_vectors: dict = {}
    detail_results = extractor.complete_batch(items, dino_vectors_out=dino_vectors)
    diagnostic_scorer = select_scorer(pipeline_settings, namespace=namespace)

    for index, ((ctx, ocr_result), detail) in enumerate(zip(owners, detail_results, strict=True)):
        record = ctx.records[ocr_result.image_path.name]
        record.stage_reached = "features"
        if isinstance(detail, Exception):
            record.rejection_reason = f"feature_error: {detail}"
            record.image_path = _copy_with_reason(
                ocr_result.image_path, ctx.out_dir / "errored", "feature_error"
            )
            continue
        record.features = detail
        try:
            _, record.contributions = diagnostic_scorer.score(record.features)
        except Exception as exc:
            logger.warning(
                "BATCH DETAIL SCORE FAILED | movie=%s | file=%s | %s",
                ctx.title,
                ocr_result.image_path.name,
                exc,
            )
        record.stage_reached = "gate"
        decision = gate.evaluate_detail(record.features)
        record.gate_decision = "passed" if decision.passed else "gated"
        record.gate_reason = decision.reason
        if decision.passed:
            if pipeline_settings.STACK_ENABLED:
                record.embedding = _stack_signal_value(
                    record, ocr_result.image_path, dino_vectors.get(index)
                )
            ctx.passed.append(record)
        else:
            record.rejection_reason = decision.reason
            ctx.gated.append(record)

    passed_total = 0
    for ctx in contexts:
        ctx.counts["feature_survivors"] = len(ctx.passed)
        passed_total += len(ctx.passed)
    _emit_global(progress, "detail-features", "end", survivors=passed_total)


def _rank(ctx: _BatchMovie, scorer, progress: ProgressCallback | None) -> None:
    """Place gated copies and rank the survivors for one movie."""
    if ctx.status == "cancelled":
        return
    place_gated(ctx.gated, ctx.out_dir / "gated")
    ctx.counts["gated"] = len(ctx.gated)

    if not ctx.passed:
        ctx.status = "flagged_manual"
        logger.warning("BATCH | %s flagged — no survivors before ranking", ctx.title)
        return

    _emit(progress, ctx, "rank", "start", total=len(ctx.passed))
    try:
        ranked = scorer.rank(ctx.passed)
        # Stage 6b: group same-design variants into stacks (auto-pick = 1A).
        if pipeline_settings.STACK_ENABLED:
            assign_stacks(ranked)
        ctx.official_pick = apply_official_pick(
            ranked,
            ctx.fetch.primary_name if ctx.fetch is not None else None,
            enabled=_official_pick_enabled(ctx.media_type, pipeline_settings),
            fallback=pipeline_settings.OFFICIAL_PICK_FALLBACK,
        )
    except Exception as exc:
        ctx.status = "failed"
        ctx.error = str(exc)
        logger.warning("BATCH RANK FAILED | movie=%s | %s", ctx.title, exc)
        _emit(progress, ctx, "rank", "end", survivors=0)
        return
    ctx.ranked = ranked
    ctx.counts["ranked"] = len(ranked)
    ctx.status = "completed"
    _emit(progress, ctx, "rank", "end", survivors=len(ranked))


# ---------------------------------------------------------------------------
# Progress + persistence helpers
# ---------------------------------------------------------------------------


def _emit(
    progress: ProgressCallback | None,
    ctx: _BatchMovie,
    stage: str,
    state: str,
    *,
    total: int | None = None,
    survivors: int | None = None,
    done: int | None = None,
) -> None:
    if progress is None:
        return
    progress(
        ProgressEvent(
            stage=stage,
            state=state,
            total=total,
            survivors=survivors,
            done=done,
            movie_id=ctx.movie_id,
            title=ctx.title,
            movie_index=ctx.index,
            movie_total=ctx.total,
        )
    )


def _emit_global(
    progress: ProgressCallback | None,
    stage: str,
    state: str,
    *,
    total: int | None = None,
    survivors: int | None = None,
    done: int | None = None,
) -> None:
    if progress is None:
        return
    progress(ProgressEvent(stage=stage, state=state, total=total, survivors=survivors, done=done))


async def _persist_running(contexts: list[_BatchMovie], job_id: str) -> None:
    factory = _get_session_factory()
    async with factory() as db:
        for ctx in contexts:
            if await db.get(PipelineRun, ctx.run_id) is None:
                db.add(
                    PipelineRun(
                        run_id=ctx.run_id,
                        status="running",
                        output_dir=str(ctx.out_dir),
                        batch_id=job_id,
                        **_run_fk_kwargs(ctx),
                    )
                )
        await db.commit()


_TERMINAL = {"completed", "flagged_manual", "failed", "cancelled"}


async def _finalize(
    contexts: list[_BatchMovie], *, job_id: str, scorer_name: str, cancelled: bool
) -> dict[str, object]:
    factory = _get_session_factory()
    summary: dict[str, int] = {}
    run_ids: list[str] = []
    async with factory() as db:
        for ctx in contexts:
            # Any movie that never reached a terminal state was interrupted —
            # attribute it to cancellation when the batch was cancelled.
            if ctx.status not in _TERMINAL:
                ctx.status = "cancelled" if cancelled else "failed"
            subject = (
                None
                if ctx.media_type == "movie"
                else {
                    "series_id": ctx.series_id,
                    "season_id": ctx.season_id,
                    "season_number": ctx.season_number,
                    "title": ctx.title,
                }
            )
            payload = build_run_payload(
                movie=Movie(
                    id=ctx.movie_id if ctx.media_type == "movie" else None,
                    title=ctx.title,
                    tmdb_id=ctx.tmdb_id,
                ),
                started_at=ctx.started_at,
                status=ctx.status,
                timings=ctx.timings,
                records=ctx.records if ctx.fetch is not None else {},
                total_duration=ctx.duration,
                run_id=ctx.run_id,
                error=ctx.error,
                media_type=ctx.media_type,
                subject=subject,
                official_pick=ctx.official_pick,
            )
            archive_path = settings.runs_archive_path / f"{ctx.run_id}.json"
            try:
                write_run_json(ctx.out_dir / "pipeline_run.json", payload)
                archive_path.parent.mkdir(parents=True, exist_ok=True)
                write_run_json(archive_path, payload)
            except Exception:  # noqa: BLE001
                logger.exception("BATCH | failed to write run JSON for %s", ctx.run_id)

            run = (
                await db.execute(select(PipelineRun).where(PipelineRun.run_id == ctx.run_id))
            ).scalar_one_or_none()
            if run is not None:
                auto_pick = find_auto_pick_candidate(payload.get("candidates", []))
                run.status = ctx.status
                run.completed_at = datetime.now(UTC)
                run.scorer_name = scorer_name if ctx.ranked else None
                run.counts_json = json.dumps(ctx.counts)
                run.timings_json = json.dumps(ctx.timings)
                run.duration_seconds = round(ctx.duration, 3)
                run.archive_path = str(archive_path)
                run.auto_pick_filename = auto_pick["orig_filename"] if auto_pick else None
                run.batch_id = job_id
                run.error = ctx.error
            summary[ctx.status] = summary.get(ctx.status, 0) + 1
            run_ids.append(ctx.run_id)
        await db.commit()
    return {
        "batch_id": job_id,
        "movies": len(contexts),
        "by_status": summary,
        "run_ids": run_ids,
        "model_name": pipeline_settings.AI_MODEL,
    }
