"""Letterbox tag-drift self-heal (design §21).

Crop tags live in the MKV header and are lost if another tool remuxes the file
(§7). This scan walks every ``tagged`` row, re-reads the file, and re-applies
the stored crop when the tags have gone missing. It is best-effort: if the
installed ``mkvmerge`` can't report crop on this build, drift can't be detected
and the file is left alone (logged), never wrongly re-tagged.

Exposed on demand (``POST /api/letterbox/heal``) and run periodically from the
app lifespan when ``LETTERBOX_HEAL_ENABLED``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select

from marquee.core.letterbox_service import letterbox_service, read_applied_crop
from marquee.database import _get_session_factory
from marquee.models import Episode, LetterboxState, Movie

logger = logging.getLogger(__name__)

letterbox_heal_state: dict = {
    "last_run": None,
    "checked": 0,
    "reapplied": 0,
    "unverifiable": 0,
    "failed": 0,
}


async def letterbox_heal_scan() -> dict:
    """Re-apply crop tags that have silently gone missing from tagged files."""
    factory = _get_session_factory()
    checked = reapplied = unverifiable = failed = 0
    async with factory() as db:
        rows = (
            await db.execute(
                select(LetterboxState, Movie)
                .join(Movie, Movie.id == LetterboxState.movie_id)
                .where(
                    LetterboxState.media_type == "movie",
                    LetterboxState.status == "tagged",
                )
            )
        ).all()
        for state, movie in rows:
            checked += 1
            eligibility = letterbox_service.check_eligibility(movie)
            if eligibility.path is None or not eligibility.eligible:
                continue
            current = read_applied_crop(eligibility.path)
            if current is None:
                # Tool can't report crop on this build — can't verify drift.
                unverifiable += 1
                continue
            expected = (state.applied_crop_top or 0, state.applied_crop_bottom or 0)
            if current == expected:
                continue  # tags intact
            logger.info("LETTERBOX HEAL | tags drifted on %s — re-applying", movie.title)
            try:
                await letterbox_service.apply(
                    db,
                    movie,
                    top=state.applied_crop_top or 0,
                    bottom=state.applied_crop_bottom or 0,
                    source="heal",
                )
                reapplied += 1
            except Exception:  # noqa: BLE001
                logger.warning(
                    "LETTERBOX HEAL | re-apply failed for %s", movie.title, exc_info=True
                )
                failed += 1

        episode_rows = (
            await db.execute(
                select(LetterboxState, Episode)
                .join(Episode, Episode.id == LetterboxState.episode_id)
                .where(
                    LetterboxState.media_type == "episode",
                    LetterboxState.status == "tagged",
                )
            )
        ).all()
        rows_by_path: dict[str, list[tuple[LetterboxState, Episode]]] = {}
        for state, episode in episode_rows:
            if not episode.episode_file_path:
                continue
            rows_by_path.setdefault(episode.episode_file_path, []).append((state, episode))

        for grouped_rows in rows_by_path.values():
            checked += len(grouped_rows)
            states = [state for state, _episode in grouped_rows]
            episodes = [episode for _state, episode in grouped_rows]
            eligibility = letterbox_service.check_episode_set_eligibility(episodes)
            if eligibility.path is None or not eligibility.eligible:
                continue
            current = read_applied_crop(eligibility.path)
            if current is None:
                unverifiable += len(grouped_rows)
                continue
            expected = (states[0].applied_crop_top or 0, states[0].applied_crop_bottom or 0)
            if current == expected:
                continue
            logger.info(
                "LETTERBOX HEAL | tags drifted on episodes=%s — re-applying",
                [episode.id for episode in episodes],
            )
            try:
                await letterbox_service.apply_episode_group(
                    db,
                    episodes,
                    top=expected[0],
                    bottom=expected[1],
                    source="heal",
                )
                reapplied += len(grouped_rows)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "LETTERBOX HEAL | re-apply failed for episodes=%s",
                    [episode.id for episode in episodes],
                    exc_info=True,
                )
                failed += len(grouped_rows)

    letterbox_heal_state.update(
        last_run=datetime.now(UTC).isoformat(),
        checked=checked,
        reapplied=reapplied,
        unverifiable=unverifiable,
        failed=failed,
    )
    logger.info(
        "LETTERBOX HEAL | checked=%d reapplied=%d unverifiable=%d failed=%d",
        checked,
        reapplied,
        unverifiable,
        failed,
    )
    return {
        "checked": checked,
        "reapplied": reapplied,
        "unverifiable": unverifiable,
        "failed": failed,
    }
