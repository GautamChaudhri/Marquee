"""LetterboxManager — batch detection jobs with live SSE progress.

One process-wide singleton (``letterbox_manager``). Detection is CPU-bound
(ffmpeg decode), so this runs a bounded worker pool *independent of the poster
pipeline's GPU lock* — letterbox work and a GPU run can proceed at once; the
pool size (``LETTERBOX_MAX_PARALLEL``) caps CPU/disk pressure.

Mirrors ``RunManager``'s event model: a per-job buffer with history replay for
late SSE subscribers and a sentinel on completion. A single batch runs at a
time (a second ``start_batch`` raises) — detection of the whole library is one
job, not many.

``detect_movie`` is the shared single-file routine used by the batch loop, the
single-movie endpoint, and the upgrade webhook.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.core.letterbox_service import letterbox_service
from marquee.database import _get_session_factory
from marquee.media import binaries, letterbox_detect, letterbox_preview, probe
from marquee.media.concurrency import gated
from marquee.models import LetterboxEvent, LetterboxState, Movie

logger = logging.getLogger(__name__)

# Strong references to fire-and-forget preview-warm tasks so the event loop
# doesn't garbage-collect them mid-run; discarded on completion.
_warm_tasks: set[asyncio.Task] = set()


def _schedule_preview_warm(source_path: str, **kwargs) -> None:
    """Warm a movie's previews in the background, bounded by the ffmpeg gate.

    Detection must return as soon as its state is committed — warming up to
    ~44 frames inline blocked the HTTP response for minutes and produced 502s.
    """

    async def _run() -> None:
        try:
            await gated(letterbox_preview.warm_movie_previews, source_path, **kwargs)
        except Exception:  # noqa: BLE001 — best-effort cache warm; never crash the loop
            logger.warning("preview warm failed for movie %s", kwargs.get("movie_id"), exc_info=True)

    task = asyncio.create_task(_run())
    _warm_tasks.add(task)
    task.add_done_callback(_warm_tasks.discard)

_SENTINEL = object()
_V1_VERTICAL_CROP_RE = re.compile(r"Vertical crop amount \(per-file\):\s*(\d+)")
_V1_NOT_LETTERBOXED_RE = re.compile(r"\bis not letterboxed\b", re.IGNORECASE)
_V1_RECOMMENDED_RE = re.compile(r"Recommended crop for .*?:\s*(\d+)x(\d+)\+(-?\d+)\+(-?\d+)")


class BatchInProgressError(Exception):
    """Raised when a batch detect is requested while one is active."""

    def __init__(self, active_job_id: str):
        self.active_job_id = active_job_id
        super().__init__(f"A letterbox batch is already running: {active_job_id}")


@dataclass
class JobState:
    job_id: str
    total: int
    detector: str = "v2"
    events: list[dict] = field(default_factory=list)
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    done: bool = False
    candidate_count: int = 0
    not_letterboxed_count: int = 0
    variable_count: int = 0

    def publish(self, event: dict) -> None:
        self.events.append(event)
        for queue in self.subscribers:
            queue.put_nowait(event)

    def finish(self) -> None:
        self.done = True
        for queue in self.subscribers:
            queue.put_nowait(_SENTINEL)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        for event in self.events:
            queue.put_nowait(event)
        if self.done:
            queue.put_nowait(_SENTINEL)
        self.subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with contextlib.suppress(ValueError):
            self.subscribers.remove(queue)

    def record_status(self, status: str) -> None:
        if status == "candidate":
            self.candidate_count += 1
        elif status == "not_letterboxed":
            self.not_letterboxed_count += 1
        elif status == "variable_unsafe":
            self.variable_count += 1

    def summary(self, *, completed: int | None = None) -> dict:
        return {
            "candidate": self.candidate_count,
            "not_letterboxed": self.not_letterboxed_count,
            "variable": self.variable_count,
            "total": self.total,
            "completed": self.total if completed is None else completed,
        }


def _max_parallel() -> int:
    configured = settings.LETTERBOX_MAX_PARALLEL
    if configured and configured > 0:
        return configured
    return max(1, (os.cpu_count() or 2) - 1)


class LetterboxManager:
    def __init__(self) -> None:
        self._active_job_id: str | None = None
        self._jobs: dict[str, JobState] = {}

    # ------------------------------------------------------------------
    # Single-file detection (shared by batch / single / webhook)
    # ------------------------------------------------------------------

    def detect_movie_blocking(self, movie: Movie) -> dict:
        """Run detection for one movie (blocking — call in a thread).

        Returns a dict of ``LetterboxState`` field updates. Probing the file
        is authoritative for dimensions/duration; the DB values are a fallback.
        """
        eligibility = letterbox_service.check_eligibility(movie)
        path = eligibility.path
        if path is None:
            return {
                "status": "ineligible",
                "eligible": False,
                "ineligible_reason": eligibility.reason,
                "error": eligibility.reason,
            }

        info = probe.probe_video(path)
        width = info.width if info else movie.video_width
        height = info.height if info else movie.video_height
        duration = info.duration_s if info else None
        container = info.container if info else movie.container
        color_transfer = info.color_transfer if info else None

        if not width or not height:
            return {
                "status": "errored",
                "eligible": eligibility.eligible,
                "ineligible_reason": eligibility.reason,
                "error": "could not determine video dimensions",
                "source_width": width,
                "source_height": height,
            }

        result = letterbox_detect.detect(
            str(path),
            width=width,
            height=height,
            duration_s=duration,
            color_transfer=color_transfer,
        )
        return {
            "status": result.status,
            "confidence": result.confidence,
            "eligible": eligibility.eligible,
            "ineligible_reason": eligibility.reason,
            "source_width": result.source_width,
            "source_height": result.source_height,
            "recommended_crop_top": result.recommended_crop_top,
            "recommended_crop_bottom": result.recommended_crop_bottom,
            "aspect_label": result.aspect_label,
            "detect_method": result.method,
            "samples_json": json.dumps(result.samples),
            "error": result.error,
            "variable_ar": result.variable_ar,
            "variable_ar_note": result.variable_ar_note,
            "_source_path": str(path),
            "_container": container,
        }

    def detect_movie_blocking_v1(self, movie: Movie) -> dict:
        """Run the original v1 shell detector for one movie."""
        eligibility = letterbox_service.check_eligibility(movie)
        path = eligibility.path
        if path is None:
            return {
                "status": "ineligible",
                "eligible": False,
                "ineligible_reason": eligibility.reason,
                "error": eligibility.reason,
            }

        info = probe.probe_video(path)
        width = info.width if info else movie.video_width
        height = info.height if info else movie.video_height
        container = info.container if info else movie.container
        duration = info.duration_s if info else None

        if not width or not height:
            return {
                "status": "errored",
                "eligible": eligibility.eligible,
                "ineligible_reason": eligibility.reason,
                "error": "could not determine video dimensions",
                "source_width": width,
                "source_height": height,
                "_container": container,
            }

        script_path = settings._project_root / "design" / "more-features" / "04-letterbox-script.sh"
        try:
            completed = subprocess.run(
                ["bash", str(script_path), "--movie-detect-crop", binaries.safe_media_path(path)],
                capture_output=True,
                text=True,
                timeout=max(120, int(duration or 0) + 120),
                check=False,
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "errored",
                "confidence": "none",
                "eligible": eligibility.eligible,
                "ineligible_reason": eligibility.reason,
                "source_width": width,
                "source_height": height,
                "recommended_crop_top": 0,
                "recommended_crop_bottom": 0,
                "aspect_label": None,
                "detect_method": "v1_script",
                "samples_json": None,
                "error": f"v1 detector failed to start: {exc}",
                "_source_path": str(path),
                "_container": container,
            }

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        combined = "\n".join(part for part in [stdout.strip(), stderr.strip()] if part).strip()
        if completed.returncode != 0:
            return {
                "status": "errored",
                "confidence": "none",
                "eligible": eligibility.eligible,
                "ineligible_reason": eligibility.reason,
                "source_width": width,
                "source_height": height,
                "recommended_crop_top": 0,
                "recommended_crop_bottom": 0,
                "aspect_label": None,
                "detect_method": "v1_script",
                "samples_json": None,
                "error": f"v1 detector exited {completed.returncode}: {combined[:300]}",
                "_source_path": str(path),
                "_container": container,
            }

        crop_match = _V1_VERTICAL_CROP_RE.search(combined)
        if crop_match:
            crop = int(crop_match.group(1))
            effective_height = max(1, height - (crop * 2))
            return {
                "status": "candidate",
                "confidence": "high",
                "eligible": eligibility.eligible,
                "ineligible_reason": eligibility.reason,
                "source_width": width,
                "source_height": height,
                "recommended_crop_top": crop,
                "recommended_crop_bottom": crop,
                "aspect_label": letterbox_detect.aspect_label(width, effective_height),
                "detect_method": "v1_script",
                "samples_json": "[]",
                "error": None,
                "_source_path": str(path),
                "_container": container,
            }

        if _V1_NOT_LETTERBOXED_RE.search(combined):
            return {
                "status": "not_letterboxed",
                "confidence": "none",
                "eligible": eligibility.eligible,
                "ineligible_reason": eligibility.reason,
                "source_width": width,
                "source_height": height,
                "recommended_crop_top": 0,
                "recommended_crop_bottom": 0,
                "aspect_label": None,
                "detect_method": "v1_script",
                "samples_json": "[]",
                "error": None,
                "_source_path": str(path),
                "_container": container,
            }

        dims = _V1_RECOMMENDED_RE.search(combined)
        if dims:
            trimmed_height = int(dims.group(2))
            crop = max(0, round((height - trimmed_height) / 2))
            effective_height = max(1, height - (crop * 2))
            return {
                "status": "candidate",
                "confidence": "high",
                "eligible": eligibility.eligible,
                "ineligible_reason": eligibility.reason,
                "source_width": width,
                "source_height": height,
                "recommended_crop_top": crop,
                "recommended_crop_bottom": crop,
                "aspect_label": letterbox_detect.aspect_label(width, effective_height),
                "detect_method": "v1_script",
                "samples_json": "[]",
                "error": None,
                "_source_path": str(path),
                "_container": container,
            }

        return {
            "status": "errored",
            "confidence": "none",
            "eligible": eligibility.eligible,
            "ineligible_reason": eligibility.reason,
            "source_width": width,
            "source_height": height,
            "recommended_crop_top": 0,
            "recommended_crop_bottom": 0,
            "aspect_label": None,
            "detect_method": "v1_script",
            "samples_json": "[]",
            "error": f"could not parse v1 detector output: {combined[:300]}",
            "_source_path": str(path),
            "_container": container,
        }

    async def detect_and_store(
        self, db: AsyncSession, movie: Movie, *, detector: str = "v2"
    ) -> LetterboxState:
        """Detect one movie and persist its ``LetterboxState`` + event."""
        detect_fn = self.detect_movie_blocking_v1 if detector == "v1" else self.detect_movie_blocking
        updates = await gated(detect_fn, movie)
        container = updates.pop("_container", None)
        source_path = updates.pop("_source_path", None)
        if container and not movie.container:
            movie.container = container

        state = await letterbox_service.get_or_create_state(db, movie.id)
        # Don't clobber a user 'skipped'/'tagged' decision with a fresh detect
        # unless the detection says the file is gone/errored.
        for key, value in updates.items():
            setattr(state, key, value)
        state.last_detected_at = datetime.now(UTC)
        # A previously-applied crop stays applied; detection only updates the
        # recommendation. If currently tagged, keep that status.
        if state.applied_crop_top is not None and updates["status"] == "candidate":
            state.status = "tagged"
        # not_letterboxed / variable_unsafe are auto-reviewed so they leave the
        # active candidate queue.
        if updates["status"] in ("not_letterboxed", "variable_unsafe"):
            state.reviewed = True

        db.add(
            LetterboxEvent(
                movie_id=movie.id,
                action="detect",
                source="detect_v1" if detector == "v1" else "detect",
                detail=json.dumps(
                    {
                        "status": updates["status"],
                        "confidence": updates.get("confidence"),
                        "top": updates.get("recommended_crop_top"),
                        "bottom": updates.get("recommended_crop_bottom"),
                        "detector": detector,
                    }
                ),
            )
        )
        await db.commit()
        if state.status == "candidate" and source_path and state.samples_json:
            try:
                samples = json.loads(state.samples_json)
            except json.JSONDecodeError:
                samples = []
            # Fire-and-forget: warming ~44 frames inline blocked the response
            # for minutes (502s). On-demand /preview renders what the user opens.
            _schedule_preview_warm(
                source_path,
                movie_id=movie.id,
                samples=samples,
                crop_top=state.recommended_crop_top or 0,
                crop_bottom=state.recommended_crop_bottom or 0,
                height=state.source_height,
            )
        return state

    # ------------------------------------------------------------------
    # Batch lifecycle
    # ------------------------------------------------------------------

    @property
    def active_job_id(self) -> str | None:
        return self._active_job_id

    def get_state(self, job_id: str) -> JobState | None:
        return self._jobs.get(job_id)

    async def start_batch(self, movie_ids: list[int], *, detector: str = "v2") -> str:
        if self._active_job_id is not None:
            raise BatchInProgressError(self._active_job_id)
        job_id = uuid4().hex
        self._active_job_id = job_id
        self._jobs[job_id] = JobState(job_id=job_id, total=len(movie_ids), detector=detector)
        asyncio.create_task(self._execute_batch(job_id, movie_ids))
        return job_id

    async def _execute_batch(self, job_id: str, movie_ids: list[int]) -> None:
        state = self._jobs[job_id]
        semaphore = asyncio.Semaphore(_max_parallel())
        completed = 0
        lock = asyncio.Lock()
        factory = _get_session_factory()

        async def worker(movie_id: int) -> None:
            nonlocal completed
            async with semaphore:
                try:
                    async with factory() as db:
                        movie = (
                            await db.execute(select(Movie).where(Movie.id == movie_id))
                        ).scalar_one_or_none()
                        if movie is None:
                            result_status = "missing"
                        else:
                            stored = await self.detect_and_store(db, movie, detector=state.detector)
                            result_status = stored.status
                except Exception as exc:  # noqa: BLE001 — one bad file mustn't kill the batch
                    logger.exception("letterbox detect failed for movie %s", movie_id)
                    result_status = f"error: {exc}"
                async with lock:
                    completed += 1
                    state.record_status(result_status)
                    state.publish(
                        {
                            "job_id": job_id,
                            "movie_id": movie_id,
                            "completed": completed,
                            "total": state.total,
                            "status": result_status,
                            "detector": state.detector,
                        }
                    )

        try:
            await asyncio.gather(*(worker(mid) for mid in movie_ids))
        finally:
            state.publish(
                {
                    "job_id": job_id,
                    "state": "done",
                    "completed": completed,
                    "total": state.total,
                    "detector": state.detector,
                    "summary": state.summary(completed=completed),
                }
            )
            state.finish()
            self._active_job_id = None
            logger.info("LETTERBOX BATCH | job=%s | done %d/%d", job_id, completed, state.total)


letterbox_manager = LetterboxManager()
