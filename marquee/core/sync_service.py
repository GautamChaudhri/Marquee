"""Sync service — pulls media metadata from Radarr/Sonarr into the local DB.

Design:
  - Each entity type syncs independently (movies, series, seasons, episodes).
  - Existing rows are bulk-loaded and indexed by external ID → O(1) lookup.
  - Sync NEVER overwrites poster columns — those belong to the pipeline.
  - Poster existence is checked during sync (no separate scanner pass).
  - Paths are validated through ``safe_translate_and_validate()`` before use.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.core.arr_clients.radarr_client import RadarrClient
from marquee.core.arr_clients.sonarr_client import SonarrClient
from marquee.core.path_utils import safe_translate_and_validate
from marquee.core.poster_sources.tmdb import TMDBClient
from marquee.models import Episode, Movie, Season, Series

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class SyncResult:
    """Counts for one entity type after a sync pass."""

    created: int = 0
    updated: int = 0
    errors: int = 0

    @property
    def total(self) -> int:
        return self.created + self.updated


@dataclass
class SeriesSyncResult:
    """Aggregate result from syncing a series group (series + seasons + episodes)."""

    series: SyncResult = field(default_factory=SyncResult)
    seasons: SyncResult = field(default_factory=SyncResult)
    episodes: SyncResult = field(default_factory=SyncResult)


@dataclass
class SyncReport:
    """Aggregate result returned by ``sync_all()``."""

    movies: SyncResult = field(default_factory=SyncResult)
    series: SyncResult = field(default_factory=SyncResult)
    seasons: SyncResult = field(default_factory=SyncResult)
    episodes: SyncResult = field(default_factory=SyncResult)
    duration_seconds: float = 0.0


# ---------------------------------------------------------------------------
# SyncService
# ---------------------------------------------------------------------------


class SyncService:
    """Orchestrates full *arr → database sync.

    All constructor arguments are optional — if a client is ``None``,
    the corresponding sync step is silently skipped.
    """

    def __init__(
        self,
        db: AsyncSession,
        radarr: RadarrClient | None = None,
        sonarr: SonarrClient | None = None,
        tmdb: TMDBClient | None = None,
    ):
        self.db = db
        self.radarr = radarr
        self.sonarr = sonarr
        self.tmdb = tmdb

    # ── Public API ───────────────────────────────────────────────────

    async def sync_all(self) -> SyncReport:
        """Run every sync step and return an aggregate report."""
        report = SyncReport()
        t0 = time.monotonic()

        if self.radarr:
            report.movies = await self._sync_movies()

        if self.sonarr:
            sr = await self._sync_series()
            report.series = sr.series
            report.seasons = sr.seasons
            report.episodes = sr.episodes

        report.duration_seconds = round(time.monotonic() - t0, 2)
        return report

    # ── Movies ───────────────────────────────────────────────────────

    async def _sync_movies(self) -> SyncResult:
        """Sync all movies from Radarr into the ``movies`` table."""
        result = SyncResult()

        raw_movies = await self.radarr.get_movies()

        # Index existing rows by radarr_id → O(1) lookups
        existing = {
            m.radarr_id: m
            for m in (await self.db.execute(select(Movie))).scalars()
        }

        for data in raw_movies:
            radarr_id = data["id"]
            try:
                # Skip entries with missing required fields
                if not data.get("title"):
                    logger.warning("Skipping movie radarr_id=%s — missing title", radarr_id)
                    result.errors += 1
                    continue

                movie = existing.get(radarr_id)
                if movie is None:
                    movie = Movie(radarr_id=radarr_id)
                    self.db.add(movie)
                    result.created += 1
                else:
                    result.updated += 1

                # ── Identity ──────────────────────────────────────────
                movie.title = data["title"]
                movie.year = data.get("year", 0)
                movie.tmdb_id = data.get("tmdbId")
                movie.imdb_id = data.get("imdbId")
                movie.folder_path = data.get("path") or ""

                # ── Filesystem ────────────────────────────────────────
                movie_file = data.get("movieFile") or {}
                movie.movie_file_path = movie_file.get("relativePath")

                # ── Quality ───────────────────────────────────────────
                movie.quality_profile_id = data.get("qualityProfileId")

                # ── Poster existence ──────────────────────────────────
                await self._check_existing_poster(movie)

            except Exception:
                logger.error(
                    "Error syncing movie radarr_id=%s", radarr_id, exc_info=True
                )
                result.errors += 1

        await self.db.commit()
        return result

    # ── Series ───────────────────────────────────────────────────────

    async def _sync_series(self) -> SeriesSyncResult:
        """Sync all TV series from Sonarr into ``series``, ``seasons``, and ``episodes``."""
        result = SeriesSyncResult()

        raw_series = await self.sonarr.get_series()

        existing = {
            s.sonarr_id: s
            for s in (await self.db.execute(select(Series))).scalars()
        }

        for data in raw_series:
            sonarr_id = data["id"]
            try:
                # Guard before creating the row: a half-initialized Series
                # with a NULL title would fail the NOT NULL constraint at
                # commit time and abort the whole sync, not just this entry.
                if not data.get("title"):
                    logger.warning(
                        "Skipping series sonarr_id=%s — missing title", sonarr_id
                    )
                    result.series.errors += 1
                    continue

                series = existing.get(sonarr_id)
                if series is None:
                    series = Series(sonarr_id=sonarr_id)
                    self.db.add(series)
                    result.series.created += 1
                else:
                    result.series.updated += 1

                # ── Identity ──────────────────────────────────────────
                series.title = data["title"]
                series.year = data.get("year", 0)
                series.tvdb_id = data.get("tvdbId") or None
                series.imdb_id = data.get("imdbId")
                # tmdb_id left NULL — populated in Phase N when adding
                # more poster sources that require it.

                # ── Filesystem ────────────────────────────────────────
                series.series_path = data.get("path") or ""
                series.season_count = len(data.get("seasons") or [])

                # ── Quality ───────────────────────────────────────────
                series.quality_profile_id = data.get("qualityProfileId")

                # ── Poster existence ──────────────────────────────────
                await self._check_existing_poster(series)

                # ── Flush so children can reference series.id ─────────
                await self.db.flush()

                # ── Children ──────────────────────────────────────────
                sr = await self._sync_seasons(series, data.get("seasons") or [])
                result.seasons.created += sr.created
                result.seasons.updated += sr.updated
                result.seasons.errors += sr.errors

                er = await self._sync_episodes(series)
                result.episodes.created += er.created
                result.episodes.updated += er.updated
                result.episodes.errors += er.errors

            except Exception:
                logger.error(
                    "Error syncing series sonarr_id=%s", sonarr_id, exc_info=True
                )
                result.series.errors += 1

        await self.db.commit()
        return result

    # ── Seasons ──────────────────────────────────────────────────────

    async def _sync_seasons(self, series: Series, sonarr_seasons: list[dict]) -> SyncResult:
        """Upsert season rows from the Sonarr series response.

        Sonarr includes a ``seasons`` array in the ``/api/v3/series``
        response — no extra API call needed.
        """
        result = SyncResult()

        # Index existing seasons by season_number
        existing_rows = (
            await self.db.execute(
                select(Season).where(Season.series_id == series.id)
            )
        ).scalars().all()
        existing = {s.season_number: s for s in existing_rows}

        for sdata in sonarr_seasons:
            season_num = sdata.get("seasonNumber", 0)
            # Skip "Specials" (season 0) and invalid numbers
            if season_num < 1:
                continue

            season = existing.get(season_num)
            if season is None:
                season = Season(series_id=series.id, season_number=season_num)
                self.db.add(season)
                result.created += 1
            else:
                result.updated += 1

            # tmdb_id for seasons is populated in Phase N
            await self._check_existing_poster(season, series=series)

        return result

    # ── Episodes ─────────────────────────────────────────────────────

    async def _sync_episodes(self, series: Series) -> SyncResult:
        """Sync episode rows for a single series from Sonarr.

        Sonarr has no "all episodes" endpoint — we must query per-series.
        For a personal library (~10-100 series), the N API calls are
        acceptable.  Episode file paths come from the episode-file join.
        """
        result = SyncResult()

        raw_episodes = await self.sonarr.get_episodes(series.sonarr_id)
        raw_files = await self.sonarr.get_episode_files(series.sonarr_id)

        # Build episode-file lookup
        file_by_id: dict[int, dict] = {f["id"]: f for f in raw_files}

        # Index existing episodes by sonarr_episode_id
        existing_rows = (
            await self.db.execute(
                select(Episode).where(Episode.series_id == series.id)
            )
        ).scalars().all()
        existing = {e.sonarr_episode_id: e for e in existing_rows}

        for edata in raw_episodes:
            ep_id = edata["id"]
            episode = existing.get(ep_id)
            if episode is None:
                episode = Episode(
                    series_id=series.id,
                    sonarr_episode_id=ep_id,
                )
                self.db.add(episode)
                result.created += 1
            else:
                result.updated += 1

            episode.season_number = edata.get("seasonNumber", 0)
            episode.episode_number = edata.get("episodeNumber", 0)
            episode.title = edata.get("title")

            # Resolve file path from episode file
            file_id = edata.get("episodeFileId")
            if file_id and file_id in file_by_id:
                episode.episode_file_path = file_by_id[file_id].get("path")

        return result

    # ── Poster existence check ───────────────────────────────────────

    async def _check_existing_poster(
        self,
        entity: Movie | Series | Season,
        *,
        series: Series | None = None,
    ) -> None:
        """Check if a poster already exists on disk for *entity*.

        If found, sets ``poster_path`` to the validated absolute path.
        If a previously recorded poster file no longer exists (e.g. Radarr
        deleted the folder during an upgrade), ``poster_path`` is cleared so
        the NULL-means-needs-poster invariant holds and the item is queued
        for re-selection instead of silently staying "complete".
        Does NOT set ``poster_ai_selected`` (the pipeline didn't pick it).
        """
        try:
            expected = _resolve_poster_path(entity, series=series)
        except ValueError:
            return  # path validation failed — skip

        if expected is not None and expected.exists():
            entity.poster_path = str(expected)
            return

        if entity.poster_path and not Path(entity.poster_path).exists():
            logger.info(
                "Poster file missing on disk — clearing stale poster_path: %s",
                entity.poster_path,
            )
            entity.poster_path = None


# ---------------------------------------------------------------------------
# Poster path resolution (module-level — stateless)
# ---------------------------------------------------------------------------


def _resolve_poster_path(
    entity: Movie | Series | Season,
    *,
    series: Series | None = None,
) -> Path | None:
    """Compute the expected poster file path for a Movie, Series, or Season.

    For Seasons, the *series* kwarg must be provided so we know the show's
    root folder (season posters live in the show root, not season subfolders).
    """
    if isinstance(entity, Movie):
        source = "radarr"
        folder = _validate_folder(entity.folder_path, source=source)
        if folder is None:
            return None
        filename = _build_movie_poster_filename(entity)
        return folder / filename

    if isinstance(entity, Season):
        source = "sonarr"
        if series is None:
            return None
        folder = _validate_folder(series.series_path, source=source)
        if folder is None:
            return None
        filename = settings.SEASON_POSTER_FORMAT.format(season=entity.season_number)
        return folder / filename

    # Series
    source = "sonarr"
    folder = _validate_folder(entity.series_path, source=source)
    if folder is None:
        return None
    return folder / settings.SERIES_POSTER_FORMAT


def _validate_folder(raw_path: str, *, source: str = "radarr") -> Path | None:
    """Translate and validate a folder path from an *arr API.

    Returns the resolved ``Path``, or ``None`` if validation fails.
    """
    try:
        return safe_translate_and_validate(raw_path, source=source)
    except ValueError:
        logger.warning("Path validation failed for: %s (source=%s)", raw_path, source)
        return None


def _build_movie_poster_filename(movie: Movie) -> str:
    """Resolve the movie poster filename from the configured format."""
    fmt = settings.MOVIE_POSTER_FORMAT

    if "{movie_basename}" in fmt and movie.movie_file_path:
        basename = Path(movie.movie_file_path).stem
        return fmt.replace("{movie_basename}", basename)

    return fmt
