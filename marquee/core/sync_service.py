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
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.core.arr_clients.radarr_client import RadarrClient
from marquee.core.arr_clients.sonarr_client import SonarrClient
from marquee.core.path_utils import safe_translate_and_validate
from marquee.core.poster_sources.tmdb import TMDBClient
from marquee.models import (
    Episode,
    EpisodeMediaFile,
    MediaFile,
    Movie,
    Season,
    Series,
)

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

                # Radarr returns genres as a list of strings; store the whole
                # list (powers label diversity + the taste map).
                genres = data.get("genres")
                movie.genres = genres if isinstance(genres, list) else None

                # ── Filesystem ────────────────────────────────────────
                movie_file = data.get("movieFile") or {}
                movie.movie_file_path = movie_file.get("relativePath")

                # ── Encoded video (letterbox pre-filter, design 04) ───
                width, height, container = _extract_media_info(movie_file)
                if width and height:
                    movie.video_width = width
                    movie.video_height = height
                if container:
                    movie.container = container

                # ── HDR / Dolby Vision (frontend G2 badges) ───────────
                has_hdr, has_dv = _extract_hdr(movie_file)
                if has_hdr is not None:
                    movie.has_hdr = has_hdr
                    movie.has_dv = has_dv

                # ── Quality ───────────────────────────────────────────
                movie.quality_profile_id = data.get("qualityProfileId")

                # ── Poster existence ──────────────────────────────────
                await self._check_existing_poster(movie)

                # ── Physical media-file row (design 03 §19.3) ─────────
                await self.db.flush()  # assign movie.id for new rows
                await _upsert_movie_media_file(self.db, movie, movie_file)

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

        # ── Physical media-file rows + episode associations (§19.3) ──
        await self.db.flush()  # assign episode.id for new rows
        await _upsert_episode_media_files(self.db, raw_episodes, file_by_id)

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


async def _upsert_movie_media_file(db: AsyncSession, movie: Movie, movie_file: dict) -> None:
    """Upsert the active ``MediaFile`` for a movie from its Radarr ``movieFile``.

    Keyed by the native ``movieFile.id`` when available, falling back to a
    path-derived key. One active row per movie; older rows are marked inactive.
    """
    file_id = movie_file.get("id")
    path = movie_file.get("path")
    relative = movie_file.get("relativePath")
    if not path and movie.folder_path and relative:
        path = str(Path(movie.folder_path) / relative)
    if not path:
        return  # no file yet (movie monitored but not downloaded)

    source_key = (
        f"radarr:movie-file:{file_id}" if file_id else f"radarr:movie:{movie.id}"
    )
    container = Path(path).suffix.lstrip(".").lower() or None
    size = movie_file.get("size")

    existing = (
        await db.execute(
            select(MediaFile).where(
                MediaFile.movie_id == movie.id, MediaFile.is_active.is_(True)
            )
        )
    ).scalars().all()

    current = next((m for m in existing if m.path == path), None)
    for other in existing:
        if other is not current:
            other.is_active = False  # replaced/old file → keep for history

    if current is None:
        current = MediaFile(movie_id=movie.id, source="radarr", source_key=source_key, path=path)
        db.add(current)
    current.source_key = source_key
    current.source_file_id = file_id
    current.path = path
    current.relative_path = relative
    current.container = container
    current.size_bytes = size
    current.is_active = True
    current.last_seen_at = datetime.now(UTC)


async def _upsert_episode_media_files(
    db: AsyncSession, raw_episodes: list[dict], file_by_id: dict[int, dict]
) -> None:
    """Upsert one MediaFile per Sonarr episode-file and associate every Episode.

    Correctly represents multi-episode files (many Episode rows → one file).
    """
    # Map each episode-file id to the set of Episode rows that reference it.
    eps_by_file: dict[int, list[Episode]] = {}
    for edata in raw_episodes:
        file_id = edata.get("episodeFileId")
        if not file_id or file_id not in file_by_id:
            continue
        ep = (
            await db.execute(
                select(Episode).where(Episode.sonarr_episode_id == edata["id"])
            )
        ).scalar_one_or_none()
        if ep is not None:
            eps_by_file.setdefault(file_id, []).append(ep)

    for file_id, episodes in eps_by_file.items():
        fdata = file_by_id[file_id]
        path = fdata.get("path")
        if not path:
            continue
        source_key = f"sonarr:episode-file:{file_id}"
        media_file = (
            await db.execute(select(MediaFile).where(MediaFile.source_key == source_key))
        ).scalar_one_or_none()
        if media_file is None:
            media_file = MediaFile(source="sonarr", source_key=source_key, path=path)
            db.add(media_file)
        media_file.source_file_id = file_id
        media_file.path = path
        media_file.relative_path = fdata.get("relativePath")
        media_file.container = Path(path).suffix.lstrip(".").lower() or None
        media_file.size_bytes = fdata.get("size")
        media_file.is_active = True
        media_file.last_seen_at = datetime.now(UTC)
        await db.flush()  # assign media_file.id

        for ep in episodes:
            link = (
                await db.execute(
                    select(EpisodeMediaFile).where(
                        EpisodeMediaFile.episode_id == ep.id,
                        EpisodeMediaFile.media_file_id == media_file.id,
                    )
                )
            ).scalar_one_or_none()
            if link is None:
                db.add(
                    EpisodeMediaFile(episode_id=ep.id, media_file_id=media_file.id)
                )


def _extract_media_info(movie_file: dict) -> tuple[int | None, int | None, str | None]:
    """Pull encoded width/height + container from a Radarr ``movieFile``.

    Radarr's ``mediaInfo`` may carry ``width``/``height`` directly or only a
    ``resolution`` string like ``"1920x1080"``; the container is derived from
    the file extension. All fields are best-effort — missing data just leaves
    the pre-filter to fall back to an on-demand ffprobe.
    """
    media_info = movie_file.get("mediaInfo") or {}
    width = media_info.get("width")
    height = media_info.get("height")
    if (not width or not height) and isinstance(media_info.get("resolution"), str):
        parts = media_info["resolution"].lower().split("x")
        if len(parts) == 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
            width, height = int(parts[0].strip()), int(parts[1].strip())

    try:
        width = int(width) if width else None
        height = int(height) if height else None
    except (TypeError, ValueError):
        width = height = None

    container = None
    rel = movie_file.get("relativePath") or movie_file.get("path")
    if rel:
        suffix = Path(rel).suffix.lstrip(".").lower()
        container = suffix or None

    return width, height, container


def _extract_hdr(movie_file: dict) -> tuple[bool | None, bool | None]:
    """Derive ``(has_hdr, has_dv)`` from a Radarr ``movieFile.mediaInfo``.

    Radarr exposes ``videoDynamicRangeType`` (e.g. ``"DV"``, ``"HDR10"``,
    ``"HDR10Plus"``, ``"HLG"``, ``"PQ"``, ``"DV HDR10"``) and/or
    ``videoDynamicRange`` (``"HDR"``/``"SDR"``/``""``). Returns ``(None, None)``
    when nothing is reported, leaving the columns NULL ("not checked") rather
    than asserting SDR. Booleans can't distinguish HDR10+ from HDR10.
    """
    media_info = movie_file.get("mediaInfo") or {}
    range_type = (media_info.get("videoDynamicRangeType") or "").upper()
    range_str = (media_info.get("videoDynamicRange") or "").upper()

    if not range_type and not range_str:
        return None, None

    has_dv = "DV" in range_type or "DOLBY" in range_type
    has_hdr = (
        any(tag in range_type for tag in ("HDR10", "HLG", "PQ", "HDR"))
        or range_str == "HDR"
    )
    if not has_dv and not has_hdr:
        # Explicitly reported and neither HDR nor DV → SDR.
        return False, False
    return has_hdr, has_dv


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
