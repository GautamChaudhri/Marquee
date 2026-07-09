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
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.core.letterbox_prefilter import (
    episode_state_has_detector_truth,
    prefilter_category_episode,
    refresh_letterbox_prefilter_for_episode,
)
from marquee.core.letterbox_service import letterbox_service
from marquee.core.media_files import MediaFileUnavailableError, resolve_media_file
from marquee.database import _get_session_factory
from marquee.media import binaries, letterbox_detect, letterbox_preview, probe
from marquee.media.concurrency import gated
from marquee.models import (
    Episode,
    EpisodeMediaFile,
    LetterboxEvent,
    LetterboxState,
    MediaFile,
    Movie,
)

logger = logging.getLogger(__name__)

# Strong references to fire-and-forget preview-warm tasks so the event loop
# doesn't garbage-collect them mid-run; discarded on completion.
_warm_tasks: set[asyncio.Task] = set()


def _schedule_preview_warm(
    source_path: str,
    *,
    warm_func=None,
    log_subject: str | None = None,
    **kwargs,
) -> None:
    """Warm a subject's (movie or episode) previews in the background, bounded
    by the ffmpeg gate.

    Detection must return as soon as its state is committed — warming up to
    ~44 frames inline blocked the HTTP response for minutes and produced 502s.
    """

    async def _run() -> None:
        try:
            await gated(warm_func or letterbox_preview.warm_previews, source_path, **kwargs)
        except Exception:  # noqa: BLE001 — best-effort cache warm; never crash the loop
            logger.warning(
                "preview warm failed for %s",
                log_subject or kwargs.get("subject_key") or kwargs.get("movie_id"),
                exc_info=True,
            )

    task = asyncio.create_task(_run())
    _warm_tasks.add(task)
    task.add_done_callback(_warm_tasks.discard)


_SENTINEL = object()
_V1_VERTICAL_CROP_RE = re.compile(r"Vertical crop amount \(per-file\):\s*(\d+)")
_V1_NOT_LETTERBOXED_RE = re.compile(r"\bis not letterboxed\b", re.IGNORECASE)
_V1_RECOMMENDED_RE = re.compile(r"Recommended crop for .*?:\s*(\d+)x(\d+)\+(-?\d+)\+(-?\d+)")


async def _emit_child_progress(db: AsyncSession, parent_job_id: str | None, detail: dict) -> None:
    """Best-effort telemetry; a progress failure must not fail detection."""
    if not parent_job_id:
        return
    try:
        from marquee.core.jobs import job_manager  # noqa: PLC0415
        from marquee.models import Job  # noqa: PLC0415

        parent = await db.get(Job, parent_job_id)
        if parent is not None:
            await job_manager.emit(db, parent, state="child_progress", detail=detail)
    except Exception:  # noqa: BLE001 - state detection takes precedence over UI telemetry
        logger.exception("could not emit letterbox child progress")


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


@dataclass(frozen=True)
class EpisodeBatchItem:
    episode_id: int
    series_id: int
    season_number: int
    episode_number: int
    media_file_id: int | None
    title: str | None = None
    episode_file_path: str | None = None
    video_width: int | None = None
    video_height: int | None = None


def _episode_sort_key(item: EpisodeBatchItem) -> tuple[int, int, int]:
    return (item.season_number, item.episode_number, item.episode_id)


def _episode_group_key(item: EpisodeBatchItem) -> tuple[str, int]:
    if item.media_file_id is not None:
        return ("media", item.media_file_id)
    return ("episode", item.episode_id)


def group_episode_items_by_media_file(
    items: list[EpisodeBatchItem],
) -> list[list[EpisodeBatchItem]]:
    groups: dict[tuple[str, int], list[EpisodeBatchItem]] = {}
    for item in sorted(items, key=_episode_sort_key):
        groups.setdefault(_episode_group_key(item), []).append(item)
    return list(groups.values())


def select_season_sample_episodes(
    items: list[EpisodeBatchItem],
    *,
    count: int,
) -> list[EpisodeBatchItem]:
    """Pick first/middle/last downloaded episodes, distinct by media file."""
    ordered = sorted(
        [item for item in items if item.season_number > 0 and item.episode_file_path],
        key=_episode_sort_key,
    )
    unique: list[EpisodeBatchItem] = []
    seen: set[tuple[str, int]] = set()
    for item in ordered:
        key = _episode_group_key(item)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    if len(unique) <= count:
        return unique
    if count <= 1:
        return [unique[len(unique) // 2]]
    indices = sorted({round(index * (len(unique) - 1) / (count - 1)) for index in range(count)})
    return [unique[index] for index in indices]


def _episode_result_has_real_bar(state: LetterboxState) -> bool:
    return state.status in {"candidate", "variable_unsafe", "tagged", "reencoded"} or (
        (state.recommended_crop_top or 0) > 0 or (state.recommended_crop_bottom or 0) > 0
    )


class LetterboxManager:
    def __init__(self) -> None:
        self._active_job_id: str | None = None
        self._jobs: dict[str, JobState] = {}

    # ------------------------------------------------------------------
    # Single-file detection (shared by batch / single / webhook)
    # ------------------------------------------------------------------

    def detect_movie_blocking(self, movie: Movie, *, thorough: bool = False) -> dict:
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
            codec=info.codec if info else None,
            pix_fmt=info.pix_fmt if info else None,
            thorough=thorough,
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
        self,
        db: AsyncSession,
        movie: Movie,
        *,
        detector: str = "v2",
        thorough: bool = False,
        parent_job_id: str | None = None,
    ) -> LetterboxState:
        """Detect one movie and persist its ``LetterboxState`` + event."""
        await _emit_child_progress(
            db,
            parent_job_id,
            {"movie_id": movie.id, "title": movie.title, "stage": "probing", "progress": 10},
        )

        await _emit_child_progress(
            db,
            parent_job_id,
            {"movie_id": movie.id, "title": movie.title, "stage": "analyzing", "progress": 30},
        )

        if detector == "v1":
            updates = await gated(self.detect_movie_blocking_v1, movie)
        else:
            if thorough:
                updates = await gated(self.detect_movie_blocking, movie, thorough=True)
            else:
                updates = await gated(self.detect_movie_blocking, movie)

        await _emit_child_progress(
            db,
            parent_job_id,
            {"movie_id": movie.id, "title": movie.title, "stage": "consensus", "progress": 90},
        )
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
                media_type="movie",
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
                warm_func=letterbox_preview.warm_movie_previews,
                log_subject=letterbox_preview.movie_subject_key(movie.id),
                movie_id=movie.id,
                samples=samples,
                crop_top=state.recommended_crop_top or 0,
                crop_bottom=state.recommended_crop_bottom or 0,
                height=state.source_height,
            )
        return state

    def detect_episode_blocking(
        self,
        episode: Episode,
        *,
        path: str,
        video_width: int | None = None,
        video_height: int | None = None,
        thorough: bool = False,
    ) -> dict:
        """Run detection for one episode file (blocking — call in a thread)."""
        source_path = Path(path)
        info = probe.probe_video(source_path)
        width = info.width if info else video_width or episode.video_width
        height = info.height if info else video_height or episode.video_height
        duration = info.duration_s if info else None
        container = info.container if info else source_path.suffix.lstrip(".").lower() or None
        color_transfer = info.color_transfer if info else None
        eligible = source_path.suffix.lower() == ".mkv" and os.access(source_path, os.W_OK)
        ineligible_reason = None if eligible else "not_mkv_or_read_only"

        if not width or not height:
            return {
                "status": "errored",
                "confidence": "none",
                "eligible": eligible,
                "ineligible_reason": ineligible_reason,
                "error": "could not determine video dimensions",
                "source_width": width,
                "source_height": height,
                "_source_path": str(source_path),
                "_container": container,
            }

        result = letterbox_detect.detect(
            str(source_path),
            width=width,
            height=height,
            duration_s=duration,
            color_transfer=color_transfer,
            codec=info.codec if info else None,
            pix_fmt=info.pix_fmt if info else None,
            is_tv=True,
            thorough=thorough,
        )
        return {
            "status": result.status,
            "confidence": result.confidence,
            "eligible": eligible,
            "ineligible_reason": ineligible_reason,
            "source_width": result.source_width,
            "source_height": result.source_height,
            "recommended_crop_top": result.recommended_crop_top,
            "recommended_crop_bottom": result.recommended_crop_bottom,
            "aspect_label": result.aspect_label
            or (letterbox_detect.aspect_label(width, height) if result.status == "not_letterboxed" else None),
            "detect_method": result.method,
            "samples_json": json.dumps(result.samples),
            "error": result.error,
            "variable_ar": result.variable_ar,
            "variable_ar_note": result.variable_ar_note,
            "_source_path": str(source_path),
            "_container": container,
        }

    async def detect_episode_group_and_store(
        self,
        db: AsyncSession,
        episodes: list[Episode],
        *,
        media_file_id: int,
        thorough: bool = False,
        parent_job_id: str | None = None,
    ) -> list[LetterboxState]:
        """Detect one physical file and fan the result out to every linked episode."""
        if not episodes:
            return []
        for episode in episodes:
            await refresh_letterbox_prefilter_for_episode(db, episode, now=datetime.now(UTC))

        try:
            resolved = await resolve_media_file(db, media_file_id)
        except MediaFileUnavailableError as exc:
            now = datetime.now(UTC)
            states: list[LetterboxState] = []
            for episode in episodes:
                state = (
                    await db.execute(
                        select(LetterboxState).where(
                            LetterboxState.media_type == "episode",
                            LetterboxState.episode_id == episode.id,
                        )
                    )
                ).scalar_one_or_none()
                if state is None:
                    state = LetterboxState(media_type="episode", episode_id=episode.id)
                    db.add(state)
                state.status = "ineligible"
                state.eligible = False
                state.ineligible_reason = str(exc)
                state.error = str(exc)
                state.last_detected_at = now
                db.add(
                    LetterboxEvent(
                        media_type="episode",
                        episode_id=episode.id,
                        action="error",
                        source="detect",
                        detail=json.dumps({"error": str(exc)}),
                    )
                )
                await _emit_child_progress(
                    db,
                    parent_job_id,
                    {
                        "episode_id": episode.id,
                        "title": episode.title,
                        "stage": "finished",
                        "progress": 100,
                        "status": "ineligible",
                        "error": str(exc),
                    },
                )
                states.append(state)
            await db.commit()
            return states

        probe_episode = episodes[0]
        await _emit_child_progress(
            db,
            parent_job_id,
            {
                "episode_id": probe_episode.id,
                "title": probe_episode.title,
                "stage": "probing",
                "progress": 10,
            },
        )
        updates = await gated(
            self.detect_episode_blocking,
            probe_episode,
            path=str(resolved.path),
            video_width=probe_episode.video_width,
            video_height=probe_episode.video_height,
            thorough=thorough,
        )
        await _emit_child_progress(
            db,
            parent_job_id,
            {
                "episode_id": probe_episode.id,
                "title": probe_episode.title,
                "stage": "consensus",
                "progress": 90,
            },
        )
        source_path = updates.pop("_source_path", None)
        now = datetime.now(UTC)
        stored_states: list[LetterboxState] = []
        for episode in episodes:
            state = (
                await db.execute(
                    select(LetterboxState).where(
                        LetterboxState.media_type == "episode",
                        LetterboxState.episode_id == episode.id,
                    )
                )
            ).scalar_one_or_none()
            if state is None:
                state = LetterboxState(media_type="episode", episode_id=episode.id)
                db.add(state)
            for key, value in updates.items():
                setattr(state, key, value)
            state.last_detected_at = now
            if updates["status"] in ("not_letterboxed", "variable_unsafe"):
                state.reviewed = True
            db.add(
                LetterboxEvent(
                    media_type="episode",
                    episode_id=episode.id,
                    action="detect",
                    source="detect",
                    detail=json.dumps(
                        {
                            "status": updates["status"],
                            "confidence": updates.get("confidence"),
                            "top": updates.get("recommended_crop_top"),
                            "bottom": updates.get("recommended_crop_bottom"),
                            "media_file_id": media_file_id,
                            "path": source_path,
                        }
                    ),
                )
            )
            await _emit_child_progress(
                db,
                parent_job_id,
                {
                    "episode_id": episode.id,
                    "title": episode.title,
                    "stage": "finished",
                    "progress": 100,
                    "status": state.status,
                    "confidence": state.confidence,
                },
            )
            stored_states.append(state)
        await db.commit()
        if source_path:
            samples_json = updates.get("samples_json")
            samples = json.loads(samples_json) if samples_json else []
            for state in stored_states:
                if state.status == "candidate":
                    # Fire-and-forget, same reasoning as the movie path: warming
                    # inline would block this response for however long the
                    # episode's sampled minutes take to render.
                    _schedule_preview_warm(
                        source_path,
                        subject_key=letterbox_preview.episode_subject_key(state.episode_id),
                        samples=samples,
                        crop_top=state.recommended_crop_top or 0,
                        crop_bottom=state.recommended_crop_bottom or 0,
                        height=state.source_height,
                    )
        return stored_states

    async def mark_sampled_clear(
        self,
        db: AsyncSession,
        episodes: list[Episode],
        *,
        source: str = "detect",
        parent_job_id: str | None = None,
    ) -> list[LetterboxState]:
        """Mark episodes as triaged clear without an individual scan."""
        now = datetime.now(UTC)
        states: list[LetterboxState] = []
        for episode in episodes:
            state = await refresh_letterbox_prefilter_for_episode(db, episode, now=now)
            if state is None:
                continue
            state.status = "sampled_clear"
            state.confidence = "none"
            state.recommended_crop_top = 0
            state.recommended_crop_bottom = 0
            state.aspect_label = None
            state.detect_method = "season_sample"
            state.samples_json = None
            state.error = None
            state.variable_ar = False
            state.variable_ar_note = None
            state.last_detected_at = now
            state.reviewed = False
            db.add(
                LetterboxEvent(
                    media_type="episode",
                    episode_id=episode.id,
                    action="detect",
                    source=source,
                    detail=json.dumps({"status": "sampled_clear"}),
                )
            )
            await _emit_child_progress(
                db,
                parent_job_id,
                {
                    "episode_id": episode.id,
                    "title": episode.title,
                    "stage": "finished",
                    "progress": 100,
                    "status": "sampled_clear",
                    "confidence": "none",
                },
            )
            states.append(state)
        await db.commit()
        return states

    async def detect_episode_batch_and_store(
        self,
        db: AsyncSession,
        episodes: list[Episode],
        *,
        exhaustive: bool = False,
        force: bool = False,
        use_season_triage: bool = True,
        parent_job_id: str | None = None,
    ) -> list[LetterboxState]:
        """Detect a TV scope using season triage and per-file fanout."""
        if not episodes:
            return []

        ordered_episodes = sorted(
            [episode for episode in episodes if episode.id is not None],
            key=lambda episode: (episode.season_number, episode.episode_number, episode.id or 0),
        )
        episode_ids = [episode.id for episode in ordered_episodes if episode.id is not None]
        link_rows = (
            await db.execute(
                select(EpisodeMediaFile.episode_id, MediaFile.id)
                .join(MediaFile, MediaFile.id == EpisodeMediaFile.media_file_id)
                .where(
                    EpisodeMediaFile.episode_id.in_(episode_ids),
                    MediaFile.is_active.is_(True),
                )
                .order_by(EpisodeMediaFile.episode_id, MediaFile.id)
            )
        ).all()
        media_file_by_episode: dict[int, int] = {}
        for episode_id, media_file_id in link_rows:
            media_file_by_episode.setdefault(episode_id, media_file_id)

        now = datetime.now(UTC)
        touched_states: list[LetterboxState] = []
        pending_items: list[EpisodeBatchItem] = []
        episodes_by_id = {episode.id: episode for episode in ordered_episodes if episode.id is not None}

        for episode in ordered_episodes:
            state = await refresh_letterbox_prefilter_for_episode(db, episode, now=now)
            if state is None:
                continue

            media_file_id = media_file_by_episode.get(episode.id)
            if media_file_id is None:
                state.status = "ineligible"
                state.eligible = False
                state.ineligible_reason = "no_active_media_file"
                state.error = "no_active_media_file"
                state.last_detected_at = now
                db.add(
                    LetterboxEvent(
                        media_type="episode",
                        episode_id=episode.id,
                        action="error",
                        source="detect",
                        detail=json.dumps({"error": "no_active_media_file"}),
                    )
                )
                await _emit_child_progress(
                    db,
                    parent_job_id,
                    {
                        "episode_id": episode.id,
                        "title": episode.title,
                        "stage": "finished",
                        "progress": 100,
                        "status": "ineligible",
                        "error": "no_active_media_file",
                    },
                )
                touched_states.append(state)
                continue

            # sampled_clear is detector truth, which is why exhaustive used to
            # no-op after season triage unless the caller also forced a rescan.
            if (
                not force
                and episode_state_has_detector_truth(state)
                and not (exhaustive and state.status == "sampled_clear")
            ):
                continue

            category, _prefilter = prefilter_category_episode(episode)
            if not force and category not in {"candidate", "unknown_resolution"}:
                continue

            pending_items.append(
                EpisodeBatchItem(
                    episode_id=episode.id,
                    series_id=episode.series_id,
                    season_number=episode.season_number,
                    episode_number=episode.episode_number,
                    media_file_id=media_file_id,
                    title=episode.title,
                    episode_file_path=episode.episode_file_path,
                    video_width=episode.video_width,
                    video_height=episode.video_height,
                )
            )

        async def scan_groups(groups: list[list[EpisodeBatchItem]]) -> list[LetterboxState]:
            results: list[LetterboxState] = []
            for group in groups:
                group_media_file_id = group[0].media_file_id
                if group_media_file_id is None:
                    continue
                results.extend(
                    await self.detect_episode_group_and_store(
                        db,
                        [episodes_by_id[item.episode_id] for item in group],
                        media_file_id=group_media_file_id,
                        parent_job_id=parent_job_id,
                    )
                )
            return results

        if not pending_items:
            await db.commit()
            return touched_states

        if exhaustive or force or not use_season_triage:
            touched_states.extend(await scan_groups(group_episode_items_by_media_file(pending_items)))
            return touched_states

        seasons: dict[int, list[EpisodeBatchItem]] = {}
        for item in pending_items:
            seasons.setdefault(item.season_number, []).append(item)

        if specials := seasons.pop(0, None):
            touched_states.extend(await scan_groups(group_episode_items_by_media_file(specials)))

        for season_number in sorted(seasons):
            season_items = seasons[season_number]
            season_groups = group_episode_items_by_media_file(season_items)
            sample_items = select_season_sample_episodes(
                season_items,
                count=settings.LETTERBOX_TV_SEASON_SAMPLE_EPISODES,
            )
            if not sample_items:
                touched_states.extend(await scan_groups(season_groups))
                continue

            sample_keys = {_episode_group_key(item) for item in sample_items}
            sample_groups = [
                group for group in season_groups if _episode_group_key(group[0]) in sample_keys
            ]
            remaining_groups = [
                group for group in season_groups if _episode_group_key(group[0]) not in sample_keys
            ]

            sample_states = await scan_groups(sample_groups)
            touched_states.extend(sample_states)
            if any(_episode_result_has_real_bar(state) for state in sample_states):
                touched_states.extend(await scan_groups(remaining_groups))
                continue

            remaining_episode_ids = {
                item.episode_id for group in remaining_groups for item in group if item.episode_id is not None
            }
            if not remaining_episode_ids:
                continue
            touched_states.extend(
                await self.mark_sampled_clear(
                    db,
                    [
                        episodes_by_id[episode_id]
                        for episode_id in sorted(remaining_episode_ids)
                        if episode_id in episodes_by_id
                    ],
                    source="detect",
                    parent_job_id=parent_job_id,
                )
            )

        return touched_states

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
