"""RunManager — lifecycle, live events, and DB provenance for pipeline runs.

One process-wide singleton (``run_manager``) that:

  - Serializes pipeline execution behind a lock and rejects concurrent runs
    with a 409 (the caller gets the active ``run_id`` to attach to instead).
  - Owns a process-lifetime ``FeatureExtractor`` so ONNX sessions are created
    once, not per request — fixes the VRAM growth observed after ~8 runs.
  - Bridges worker-thread progress callbacks onto per-run event queues so the
    API can stream live stage progress over SSE, with full history replay for
    late or reconnecting subscribers.
  - Writes/updates the ``pipeline_runs`` DB row and archives each run's JSON.

The extractor is reset (recreated on next run) whenever the taste profile
changes — call ``reset_extractor()`` after an incremental exemplar append or a
profile rebuild.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from marquee.config import settings
from marquee.core.poster_sources.tmdb import TMDBClient
from marquee.database import _get_session_factory
from marquee.models import Movie, PipelineRun
from marquee.pipeline.features import FeatureExtractor
from marquee.pipeline.runner import (
    ProgressEvent,
    _add_run_file_handler,
    _clear_generated_outputs,
    _remove_run_file_handler,
    _sanitise_filename,
    build_run_payload,
    fetch_and_download,
    place_outputs,
    run_sync_stages,
    write_run_json,
)

logger = logging.getLogger(__name__)

_SENTINEL = object()


class RunInProgressError(Exception):
    """Raised when a run is requested while another is active."""

    def __init__(self, active_run_id: str):
        self.active_run_id = active_run_id
        super().__init__(f"A pipeline run is already in progress: {active_run_id}")


@dataclass
class RunState:
    """In-memory event buffer + subscribers for one run."""

    run_id: str
    events: list[dict] = field(default_factory=list)
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    done: bool = False

    def publish(self, event: dict) -> None:
        """Append an event and fan it out to all current subscribers.

        Runs on the event loop (directly in ``_execute`` or via
        ``call_soon_threadsafe`` from the worker thread), so it never
        interleaves with ``subscribe`` mid-call.
        """
        self.events.append(event)
        for queue in self.subscribers:
            queue.put_nowait(event)

    def finish(self) -> None:
        self.done = True
        for queue in self.subscribers:
            queue.put_nowait(_SENTINEL)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        for event in self.events:  # replay history for late joiners
            queue.put_nowait(event)
        if self.done:
            queue.put_nowait(_SENTINEL)
        self.subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with contextlib.suppress(ValueError):
            self.subscribers.remove(queue)


class RunManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._active_run_id: str | None = None
        self._rebuild_active: bool = False
        self._extractor: FeatureExtractor | None = None
        self._runs: dict[str, RunState] = {}

    # ------------------------------------------------------------------
    # GPU mutual exclusion (pipeline runs vs taste-profile rebuilds)
    # ------------------------------------------------------------------

    def gpu_busy(self) -> str | None:
        """A short description of the active GPU job, or None if idle.

        Pipeline runs and the full taste-profile rebuild both load model
        stacks onto the GPU; on a small card they cannot run together, so
        each refuses to start while the other holds the GPU.
        """
        if self._active_run_id is not None:
            return f"pipeline run {self._active_run_id}"
        if self._rebuild_active:
            return "taste-profile rebuild"
        return None

    def begin_rebuild(self) -> None:
        """Claim the GPU for a taste rebuild; raises if a run is active."""
        if self._active_run_id is not None:
            raise RunInProgressError(self._active_run_id)
        self._rebuild_active = True

    def end_rebuild(self) -> None:
        self._rebuild_active = False

    # ------------------------------------------------------------------
    # Extractor lifecycle
    # ------------------------------------------------------------------

    def _ensure_extractor(self) -> FeatureExtractor:
        """Create + preflight once (blocking — call inside a thread)."""
        if self._extractor is None:
            extractor = FeatureExtractor()
            extractor.preflight()
            self._extractor = extractor
        return self._extractor

    def reset_extractor(self) -> None:
        """Drop the cached extractor so the next run reloads the taste profile."""
        self._extractor = None
        logger.info("RunManager | extractor reset — next run reloads the taste profile")

    def release_gpu_resources(self) -> dict:
        """Drop process-local model caches and ask Python to release memory.

        This is intentionally conservative: it only clears caches owned by this
        process. CUDA/ORT/Paddle native allocators may still keep pools alive,
        but clearing these references is the best safe in-process release path
        before handing the GPU to another component.
        """
        had_extractor = self._extractor is not None
        self._extractor = None

        ocr_cleared = False
        try:
            from marquee.pipeline import ocr_filter  # noqa: PLC0415

            if getattr(ocr_filter, "_worker_ocr", None) is not None:
                ocr_cleared = True
            ocr_filter._worker_ocr = None
        except Exception:  # noqa: BLE001
            logger.exception("RunManager | failed to clear OCR singleton")

        collected = gc.collect()
        result = {
            "extractor_cleared": had_extractor,
            "ocr_cleared": ocr_cleared,
            "gc_collected": collected,
        }
        logger.info("RunManager | released GPU resources | %s", result)
        return result

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def active_run_id(self) -> str | None:
        return self._active_run_id

    def get_state(self, run_id: str) -> RunState | None:
        return self._runs.get(run_id)

    async def start(self, *, tmdb: TMDBClient, movie: Movie) -> str:
        """Begin a run; return its run_id. Raises RunInProgressError if busy."""
        if self._active_run_id is not None:
            raise RunInProgressError(self._active_run_id)
        if self._rebuild_active:
            raise RunInProgressError("taste-profile rebuild")

        run_id = uuid4().hex
        self._active_run_id = run_id  # set synchronously — closes the race
        self._runs[run_id] = RunState(run_id=run_id)

        out_dir = settings.runs_work_path / _sanitise_filename(movie.title)

        # Persist the run row immediately so GET /runs/{id} works mid-run.
        factory = _get_session_factory()
        async with factory() as session:
            session.add(
                PipelineRun(
                    run_id=run_id,
                    movie_id=movie.id,
                    status="running",
                    output_dir=str(out_dir),
                )
            )
            await session.commit()

        # Snapshot the scalars we need so the detached Movie isn't touched later.
        asyncio.create_task(
            self._execute(
                run_id=run_id,
                tmdb=tmdb,
                movie_id=movie.id,
                movie_title=movie.title,
                movie_tmdb_id=movie.tmdb_id,
            )
        )
        return run_id

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def _execute(
        self,
        *,
        run_id: str,
        tmdb: TMDBClient,
        movie_id: int,
        movie_title: str,
        movie_tmdb_id: int | None,
    ) -> None:
        loop = asyncio.get_running_loop()
        state = self._runs[run_id]

        def progress(event: ProgressEvent) -> None:
            loop.call_soon_threadsafe(
                state.publish, {"run_id": run_id, **event.to_dict()}
            )

        out_dir = settings.runs_work_path / _sanitise_filename(movie_title)
        out_dir.mkdir(parents=True, exist_ok=True)
        _clear_generated_outputs(out_dir)
        originals_dir = out_dir / "0-originals"
        originals_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / "pipeline_run.json"
        file_handler = _add_run_file_handler(out_dir / "pipeline.log")

        # Minimal stand-in carrying the scalar columns the runner reads.
        movie = Movie(id=movie_id, title=movie_title, tmdb_id=movie_tmdb_id)

        run_started = time.perf_counter()
        started_at = datetime.now(UTC).isoformat()
        timings: dict[str, float] = {}
        records: dict = {}
        status = "failed"
        error: str | None = None
        scorer_name: str | None = None
        counts: dict[str, int] = {}

        try:
            logger.info("=" * 80)
            logger.info(
                "RUN START | run_id=%s | movie=%s | movie_id=%d | tmdb_id=%s",
                run_id,
                movie_title,
                movie_id,
                movie_tmdb_id,
            )
            if movie_tmdb_id is None:
                raise RuntimeError("Movie has no TMDB ID — run sync first")

            async with self._lock:
                fetch = await fetch_and_download(
                    tmdb=tmdb,
                    movie=movie,
                    originals_dir=originals_dir,
                    timings=timings,
                    progress=progress,
                )
                records = fetch.records

                outcome = await asyncio.to_thread(
                    self._run_stages_blocking,
                    movie_title=movie_title,
                    out_dir=out_dir,
                    fetch=fetch,
                    timings=timings,
                    progress=progress,
                )
                status = outcome.status
                counts = {**fetch.counts, **outcome.counts}

                if outcome.ranked:
                    await place_outputs(
                        outcome.ranked,
                        candidate_map=fetch.candidate_map,
                        out_dir=out_dir,
                        timings=timings,
                        progress=progress,
                    )

                # Provenance: which scorer head ranked this run (cheap re-resolve;
                # deterministic from config — same answer the stages used).
                from marquee.pipeline.scorer import select_scorer  # noqa: PLC0415

                scorer_name = select_scorer().name

        except Exception as exc:  # noqa: BLE001 — recorded as a failed run
            status = "failed"
            error = str(exc)
            logger.exception("RUN FAILED | run_id=%s | error=%s", run_id, exc)

        total_duration = time.perf_counter() - run_started
        payload = build_run_payload(
            movie=movie,
            started_at=started_at,
            status=status,
            timings=timings,
            records=records,
            total_duration=total_duration,
            run_id=run_id,
            error=error,
        )
        archive_path = settings.runs_archive_path / f"{run_id}.json"
        try:
            write_run_json(json_path, payload)
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            write_run_json(archive_path, payload)
        except Exception:
            logger.exception("RUN | failed to write run JSON for %s", run_id)

        await self._finalize_db(
            run_id=run_id,
            status=status,
            scorer_name=scorer_name,
            counts=counts,
            archive_path=str(archive_path),
            error=error,
        )

        logger.info(
            "RUN END | run_id=%s | status=%s | total=%.3fs", run_id, status, total_duration
        )
        logger.info("=" * 80)
        _remove_run_file_handler(file_handler)

        state.publish(
            {
                "run_id": run_id,
                "stage": "run",
                "state": "end",
                "status": status,
                "elapsed_s": round(total_duration, 3),
                "error": error,
            }
        )
        state.finish()
        self._active_run_id = None

    def _run_stages_blocking(self, *, movie_title, out_dir, fetch, timings, progress):
        extractor = self._ensure_extractor()
        return run_sync_stages(
            movie_title=movie_title,
            out_dir=out_dir,
            records=fetch.records,
            candidate_map=fetch.candidate_map,
            all_files=fetch.all_files,
            resolution_by_name=fetch.resolution_by_name,
            timings=timings,
            feature_extractor=extractor,
            primary_name=fetch.primary_name,
            progress=progress,
        )

    async def _finalize_db(
        self,
        *,
        run_id: str,
        status: str,
        scorer_name: str | None,
        counts: dict,
        archive_path: str,
        error: str | None,
    ) -> None:
        factory = _get_session_factory()
        async with factory() as session:
            run = (
                await session.execute(
                    select(PipelineRun).where(PipelineRun.run_id == run_id)
                )
            ).scalar_one_or_none()
            if run is None:
                return
            run.status = status
            run.completed_at = datetime.now(UTC)
            run.scorer_name = scorer_name
            run.counts_json = json.dumps(counts)
            run.archive_path = archive_path
            run.error = error
            await session.commit()

    # ------------------------------------------------------------------
    # Results access
    # ------------------------------------------------------------------

    def load_archive(self, run_id: str, archive_path: str | None = None) -> dict | None:
        """Load a run's archived JSON payload."""
        path = Path(archive_path) if archive_path else settings.runs_archive_path / f"{run_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            logger.warning("Could not read run archive %s", path)
            return None


run_manager = RunManager()
