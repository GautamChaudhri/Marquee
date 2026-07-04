"""Sync-time letterbox prefiltering from stored media dimensions."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.media.probe import prefilter_bucket
from marquee.models import Episode, LetterboxState, Movie

PREFILTER_STATUSES = {
    "prefilter_candidate",
    "prefilter_unknown",
    "prefilter_skipped",
}
DETECTOR_TRUTH_STATUSES = {
    "candidate",
    "sampled_clear",
    "not_letterboxed",
    "variable_unsafe",
    "tagged",
    "reencoded",
    "skipped",
    "ineligible",
}


def prefilter_category(movie: Movie) -> tuple[str, dict]:
    """Return the prefilter category and serializable explanation for a movie."""
    return prefilter_category_for_dimensions(movie.video_width, movie.video_height)


def prefilter_category_for_dimensions(width: int | None, height: int | None) -> tuple[str, dict]:
    """Return the prefilter category and serializable explanation for dimensions."""
    result = prefilter_bucket(width, height)
    category = "unknown_resolution" if result.reason == "unknown_resolution" else result.bucket
    aspect_ratio = round(result.aspect_ratio, 4) if result.aspect_ratio is not None else None
    return category, {
        "bucket": result.bucket,
        "category": category,
        "reason": result.reason,
        "aspect_ratio": aspect_ratio,
    }


def prefilter_category_episode(episode: Episode) -> tuple[str, dict]:
    """Return the prefilter category and serializable explanation for an episode."""
    return prefilter_category_for_dimensions(episode.video_width, episode.video_height)


def episode_has_file(episode: Episode) -> bool:
    return bool(episode.episode_file_path)


def movie_has_file(movie: Movie) -> bool:
    return bool(movie.movie_file_path)


def episode_state_has_detector_truth(state: LetterboxState | None) -> bool:
    return state_has_detector_truth(state)


def apply_prefilter_state_episode(
    state: LetterboxState,
    episode: Episode,
    *,
    category: str,
    prefilter: dict,
    now: datetime,
) -> None:
    """Populate prefilter fields for an episode without touching detector truth."""
    state.prefilter_bucket = prefilter["bucket"]
    state.prefilter_reason = prefilter["reason"]
    state.prefilter_aspect_ratio = prefilter["aspect_ratio"]
    state.last_prefiltered_at = now

    if state.last_detected_at is None:
        state.source_width = episode.video_width
        state.source_height = episode.video_height

    if not state_has_detector_truth(state):
        state.status = prefilter_status_for_category(category)
        state.confidence = None
        state.recommended_crop_top = None
        state.recommended_crop_bottom = None
        state.aspect_label = None
        state.detect_method = None
        state.samples_json = None
        state.reviewed = False
        state.error = None
        state.variable_ar = False
        state.variable_ar_note = None


def prefilter_status_for_category(category: str) -> str:
    if category == "candidate":
        return "prefilter_candidate"
    if category == "unknown_resolution":
        return "prefilter_unknown"
    return "prefilter_skipped"


def state_has_detector_truth(state: LetterboxState | None) -> bool:
    """Whether a state row represents detection/user workflow truth."""
    if state is None:
        return False
    if state.status in PREFILTER_STATUSES or state.status == "errored":
        return False
    if state.last_detected_at is not None:
        return True
    if (
        state.reviewed
        or state.applied_crop_top is not None
        or state.applied_crop_bottom is not None
    ):
        return True
    if state.status in DETECTOR_TRUTH_STATUSES:
        return state.recommended_crop_top is not None or state.status != "candidate"
    return False


def apply_prefilter_state(
    state: LetterboxState,
    movie: Movie,
    *,
    category: str,
    prefilter: dict,
    now: datetime,
) -> None:
    """Populate prefilter fields without touching detector-truth rows."""
    state.prefilter_bucket = prefilter["bucket"]
    state.prefilter_reason = prefilter["reason"]
    state.prefilter_aspect_ratio = prefilter["aspect_ratio"]
    state.last_prefiltered_at = now

    if state.last_detected_at is None:
        state.source_width = movie.video_width
        state.source_height = movie.video_height

    if not state_has_detector_truth(state):
        state.status = prefilter_status_for_category(category)
        state.confidence = None
        state.recommended_crop_top = None
        state.recommended_crop_bottom = None
        state.aspect_label = None
        state.detect_method = None
        state.samples_json = None
        state.reviewed = False
        state.error = None
        state.variable_ar = False
        state.variable_ar_note = None


async def refresh_letterbox_prefilter_for_movie(
    db: AsyncSession,
    movie: Movie,
    *,
    now: datetime | None = None,
) -> LetterboxState | None:
    """Create/update a prefilter row for present movies only.

    Radarr can sync wanted-but-not-downloaded movies with no movie file. Those
    rows stay in ``movies`` but should not enter letterbox workflow state until
    a later sync provides a file.
    """
    state = (
        await db.execute(
            select(LetterboxState).where(
                LetterboxState.media_type == "movie",
                LetterboxState.movie_id == movie.id,
            )
        )
    ).scalar_one_or_none()

    if not movie_has_file(movie):
        if state is not None and not state_has_detector_truth(state):
            await db.delete(state)
        return None

    if state is None:
        state = LetterboxState(media_type="movie", movie_id=movie.id)
        db.add(state)

    if state_has_detector_truth(state):
        return state

    category, prefilter = prefilter_category(movie)
    apply_prefilter_state(
        state,
        movie,
        category=category,
        prefilter=prefilter,
        now=now or datetime.now(UTC),
    )
    return state


async def refresh_letterbox_prefilter_for_episode(
    db: AsyncSession,
    episode: Episode,
    *,
    now: datetime | None = None,
) -> LetterboxState | None:
    """Create/update a prefilter row for present episodes only."""
    state = (
        await db.execute(
            select(LetterboxState).where(
                LetterboxState.media_type == "episode",
                LetterboxState.episode_id == episode.id,
            )
        )
    ).scalar_one_or_none()

    if not episode_has_file(episode):
        if state is not None and not state_has_detector_truth(state):
            await db.delete(state)
        return None

    if state is None:
        state = LetterboxState(media_type="episode", episode_id=episode.id)
        db.add(state)

    if state_has_detector_truth(state):
        return state

    category, prefilter = prefilter_category_episode(episode)
    apply_prefilter_state_episode(
        state,
        episode,
        category=category,
        prefilter=prefilter,
        now=now or datetime.now(UTC),
    )
    return state
