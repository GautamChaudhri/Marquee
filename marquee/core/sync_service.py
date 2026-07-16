"""Sync service — pulls media metadata from Radarr/Sonarr into the local DB.

Design:
  - Each entity type syncs independently (movies, series, seasons, episodes).
  - Existing rows are bulk-loaded and indexed by external ID → O(1) lookup.
  - Sync NEVER overwrites poster columns — those belong to the pipeline.
  - Poster existence is checked during sync (no separate scanner pass).
  - Paths are validated through ``safe_translate_and_validate()`` before use.
  - Path validation is cached (LRU 1000 entries) to avoid redundant filesystem hits.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.core.arr_clients.radarr_client import RadarrClient
from marquee.core.arr_clients.sonarr_client import SonarrClient
from marquee.core.cancellation import raise_if_cancelled
from marquee.core.letterbox_prefilter import refresh_letterbox_prefilter_for_movie
from marquee.core.media_files import compute_signature
from marquee.core.path_utils import safe_translate_and_validate
from marquee.core.poster_sources.tmdb import TMDBClient
from marquee.core.radarr_overlay import classify_hdr_flags
from marquee.core.subtitles import languages as subtitle_languages
from marquee.models import (
    Episode,
    EpisodeMediaFile,
    LetterboxEvent,
    LetterboxState,
    MediaFile,
    Movie,
    MovieCustomFormatScore,
    RadarrCustomFormat,
    RadarrProfileFormatItem,
    RadarrQualityProfile,
    Season,
    Series,
    SonarrCustomFormat,
    SonarrProfileFormatItem,
    SonarrQualityProfile,
)

logger = logging.getLogger(__name__)


def _reset_letterbox_state_for_new_file(state: LetterboxState) -> None:
    state.status = "prefilter_candidate"
    state.confidence = None
    state.recommended_crop_top = None
    state.recommended_crop_bottom = None
    state.applied_crop_top = None
    state.applied_crop_bottom = None
    state.last_applied_at = None
    state.aspect_label = None
    state.detect_method = None
    state.samples_json = None
    state.reviewed = False
    state.error = None
    state.variable_ar = False
    state.variable_ar_note = None
    state.eligible = False
    state.ineligible_reason = None
    state.last_detected_at = None
    state.source_width = None
    state.source_height = None
    state.prefilter_bucket = None
    state.prefilter_reason = None
    state.prefilter_aspect_ratio = None
    state.last_prefiltered_at = None
    state.resolved_by = None
    state.resolved_at = None
    state.original_crop_top = None
    state.original_crop_bottom = None
    state.original_aspect_label = None


def _signature_for_media_file(media_file: MediaFile) -> tuple[str | None, str | None]:
    try:
        path = safe_translate_and_validate(media_file.path, source=media_file.source)
        return compute_signature(path), None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


async def _reset_letterbox_for_media_replacement(
    db: AsyncSession,
    *,
    media_type: str,
    current_media_file: MediaFile,
    movie_id: int | None = None,
    episode_id: int | None = None,
) -> None:
    state = (
        await db.execute(
            select(LetterboxState).where(
                LetterboxState.media_type == media_type,
                LetterboxState.movie_id == movie_id,
                LetterboxState.episode_id == episode_id,
            )
        )
    ).scalar_one_or_none()
    if state is None:
        return

    current_signature, signature_error = _signature_for_media_file(current_media_file)
    if (
        state.resolved_by != "reencode"
        and state.status in {"prefilter_candidate", "prefilter_unknown", "prefilter_skipped"}
        and state.last_detected_at is None
        and state.applied_crop_top is None
        and state.applied_crop_bottom is None
        and not state.reviewed
    ):
        return

    previous_status = state.status
    previous_resolved_by = state.resolved_by
    _reset_letterbox_state_for_new_file(state)
    db.add(
        LetterboxEvent(
            media_type=media_type,
            movie_id=movie_id,
            episode_id=episode_id,
            action="reset",
            source="sync",
            detail=json.dumps(
                {
                    "reason": "media_file_replaced",
                    "previous_status": previous_status,
                    "previous_resolved_by": previous_resolved_by,
                    "current_media_file_id": current_media_file.id,
                    "current_signature": current_signature,
                    "signature_error": signature_error,
                }
            ),
        )
    )


def _needs_tmdb_enrichment(movie: Movie) -> bool:
    """Whether this movie still needs the one-time TMDB OCR metadata backfill.

    ``tagline`` is intentionally not part of the retry gate. Many movies have
    no tagline at all; retrying on every ``NULL`` tagline would re-hit TMDB on
    every sync for those rows. We still populate ``tagline`` whenever we do
    fetch details for a movie.
    """
    return bool(movie.tmdb_id) and (
        movie.director is None or movie.production_companies_json is None
    )


def _needs_tv_enrichment(series: Series) -> bool:
    """Whether this TV series still needs the one-time TMDB OCR metadata backfill."""
    return bool(series.tmdb_id) and (
        series.director is None or series.production_companies_json is None
    )


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

    async def sync_all(self, progress=None, cancel_event=None) -> SyncReport:
        """Run every sync step and return an aggregate report.

        ``progress`` is an optional ``async (stage, message) -> None`` callback
        fired at each phase boundary so the job progress bar can narrate what
        is being synced instead of spinning silently.
        """
        report = SyncReport()
        t0 = time.monotonic()
        raise_if_cancelled(cancel_event, "library sync cancelled")

        if self.radarr:
            if progress is not None:
                await progress("sync_movies", "Syncing movies from Radarr…")
            raise_if_cancelled(cancel_event, "library sync cancelled")
            report.movies = await self._sync_movies()
            raise_if_cancelled(cancel_event, "library sync cancelled")

        if self.sonarr:
            if progress is not None:
                await progress("sync_series", "Syncing series from Sonarr…")
            raise_if_cancelled(cancel_event, "library sync cancelled")
            sr = await self._sync_series()
            report.series = sr.series
            report.seasons = sr.seasons
            report.episodes = sr.episodes
            raise_if_cancelled(cancel_event, "library sync cancelled")

        report.duration_seconds = round(time.monotonic() - t0, 2)

        # Clear path validation cache after sync to avoid serving stale data
        # if *arr updates a folder path between syncs.
        _validate_folder_cached.cache_clear()
        logger.debug(
            "Path validation cache cleared (%d hits)", _validate_folder_cached.cache_info().hits
        )

        return report

    # ── Movies ───────────────────────────────────────────────────────

    async def _sync_movies(self) -> SyncResult:
        """Sync all movies from Radarr into the ``movies`` table."""
        result = SyncResult()
        now = datetime.now(UTC)

        raw_movies = await self.radarr.get_movies()
        if not isinstance(raw_movies, list):
            raise TypeError("Radarr movie response must be a complete list")
        movie_files = []
        custom_formats = []
        quality_profiles = []
        try:
            movie_files = await self.radarr.get_movie_files(
                [movie["id"] for movie in raw_movies if movie.get("id") is not None]
            )
        except Exception:
            logger.warning("Failed to sync Radarr movie files", exc_info=True)
        if not isinstance(movie_files, list):
            movie_files = []
        try:
            custom_formats = await self.radarr.get_custom_formats()
        except Exception:
            logger.warning("Failed to sync Radarr custom formats", exc_info=True)
        try:
            quality_profiles = await self.radarr.get_quality_profiles()
        except Exception:
            logger.warning("Failed to sync Radarr quality profiles", exc_info=True)
        if not isinstance(custom_formats, list):
            custom_formats = []
        if not isinstance(quality_profiles, list):
            quality_profiles = []
        custom_format_names, profile_scores_by_profile = await _sync_radarr_overlay_reference_data(
            self.db, custom_formats, quality_profiles, now
        )
        movie_file_by_movie_id = {
            movie_file["movieId"]: movie_file
            for movie_file in movie_files
            if movie_file.get("movieId") is not None
        }

        # Index existing rows by radarr_id → O(1) lookups
        existing = {m.radarr_id: m for m in (await self.db.execute(select(Movie))).scalars()}
        enrich_candidates: list[Movie] = []

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
                previous_tmdb_id = movie.tmdb_id
                movie.is_present = True
                movie.retired_at = None
                movie.last_seen_at = now

                # ── Identity ──────────────────────────────────────────
                movie.title = data["title"]
                movie.year = data.get("year", 0)
                movie.tmdb_id = data.get("tmdbId")
                if previous_tmdb_id != movie.tmdb_id:
                    movie.director = None
                    movie.production_companies_json = None
                    movie.tagline = None
                movie.imdb_id = data.get("imdbId")
                movie.folder_path = data.get("path") or ""

                # Radarr returns genres as a list of strings; store the whole
                # list (powers label diversity + the taste map).
                genres = data.get("genres")
                movie.genres = genres if isinstance(genres, list) else None

                # ── Filesystem ────────────────────────────────────────
                movie_file = movie_file_by_movie_id.get(radarr_id) or data.get("movieFile") or {}
                movie.movie_file_path = movie_file.get("relativePath")

                # ── Encoded video (letterbox pre-filter, design 04) ───
                width, height, container = _extract_media_info(movie_file)
                if width and height:
                    movie.video_width = width
                    movie.video_height = height
                if container:
                    movie.container = container

                # ── HDR / Dolby Vision (frontend G2 badges) ───────────
                hdr_type_raw, has_hdr, has_dv = _extract_hdr(movie_file)
                movie.hdr_type_raw = hdr_type_raw
                movie.has_hdr = has_hdr
                movie.has_dv = has_dv

                # ── Quality ───────────────────────────────────────────
                movie.quality_profile_id = data.get("qualityProfileId")
                quality_cutoff_not_met = movie_file.get("qualityCutoffNotMet")
                movie.quality_cutoff_met = (
                    None if quality_cutoff_not_met is None else not bool(quality_cutoff_not_met)
                )
                movie.current_cf_score = (
                    None
                    if movie_file.get("customFormatScore") is None
                    else int(movie_file.get("customFormatScore"))
                )

                # ── Poster existence ──────────────────────────────────
                await self._check_existing_poster(movie)

                # ── Physical media-file row (design 03 §19.3) ─────────
                await self.db.flush()  # assign movie.id for new rows
                await _replace_movie_custom_format_scores(
                    self.db,
                    movie.id,
                    movie_file.get("customFormats") or [],
                    custom_format_names,
                    profile_scores_by_profile.get(movie.quality_profile_id or -1, {}),
                    now,
                )
                await _upsert_movie_media_file(self.db, movie, movie_file)
                await refresh_letterbox_prefilter_for_movie(self.db, movie)
                if _needs_tmdb_enrichment(movie):
                    enrich_candidates.append(movie)

            except Exception:
                logger.error("Error syncing movie radarr_id=%s", radarr_id, exc_info=True)
                result.errors += 1

        if self.tmdb and enrich_candidates:
            await self._enrich_movies_from_tmdb(enrich_candidates)

        if result.errors == 0:
            observed_ids = {int(data["id"]) for data in raw_movies}
            for radarr_id, movie in existing.items():
                if radarr_id not in observed_ids and movie.is_present:
                    movie.is_present = False
                    movie.retired_at = now
                    media_files = (
                        await self.db.execute(
                            select(MediaFile).where(
                                MediaFile.movie_id == movie.id,
                                MediaFile.is_present.is_(True),
                            )
                        )
                    ).scalars()
                    for media_file in media_files:
                        media_file.is_present = False
                        media_file.is_active = False
                        media_file.retired_at = now

        await self.db.commit()
        return result

    async def _enrich_movies_from_tmdb(self, movies: list[Movie]) -> None:
        """Backfill OCR metadata from TMDB without blocking the sync on failures."""
        semaphore = asyncio.Semaphore(5)

        async def enrich(movie: Movie) -> None:
            tmdb_id = movie.tmdb_id
            if tmdb_id is None or not _needs_tmdb_enrichment(movie):
                return
            try:
                async with semaphore:
                    details = await self.tmdb.get_movie_details(tmdb_id)
            except Exception:
                logger.warning(
                    "TMDB enrichment failed for movie radarr_id=%s tmdb_id=%s",
                    movie.radarr_id,
                    tmdb_id,
                    exc_info=True,
                )
                return

            movie.director = details.director
            movie.production_companies_json = details.production_companies
            movie.tagline = details.tagline

        await asyncio.gather(*(enrich(movie) for movie in movies))

    # ── Series ───────────────────────────────────────────────────────

    async def _sync_series(self) -> SeriesSyncResult:
        """Sync all TV series from Sonarr into ``series``, ``seasons``, and ``episodes``."""
        result = SeriesSyncResult()
        now = datetime.now(UTC)

        raw_series = await self.sonarr.get_series()
        if not isinstance(raw_series, list):
            raise TypeError("Sonarr series response must be a complete list")

        custom_formats = []
        quality_profiles = []
        try:
            custom_formats = await self.sonarr.get_custom_formats()
        except Exception:
            logger.warning("Failed to sync Sonarr custom formats", exc_info=True)
        try:
            quality_profiles = await self.sonarr.get_quality_profiles()
        except Exception:
            logger.warning("Failed to sync Sonarr quality profiles", exc_info=True)
        if not isinstance(custom_formats, list):
            custom_formats = []
        if not isinstance(quality_profiles, list):
            quality_profiles = []
        await _sync_sonarr_overlay_reference_data(self.db, custom_formats, quality_profiles, now)

        existing = {s.sonarr_id: s for s in (await self.db.execute(select(Series))).scalars()}
        enrich_candidates = []

        for data in raw_series:
            sonarr_id = data["id"]
            try:
                # Guard before creating the row: a half-initialized Series
                # with a NULL title would fail the NOT NULL constraint at
                # commit time and abort the whole sync, not just this entry.
                if not data.get("title"):
                    logger.warning("Skipping series sonarr_id=%s — missing title", sonarr_id)
                    result.series.errors += 1
                    continue

                series = existing.get(sonarr_id)
                if series is None:
                    series = Series(sonarr_id=sonarr_id)
                    self.db.add(series)
                    result.series.created += 1
                else:
                    result.series.updated += 1
                previous_tmdb_id = series.tmdb_id
                series.is_present = True
                series.retired_at = None
                series.last_seen_at = now

                # ── Identity ──────────────────────────────────────────
                series.title = data["title"]
                series.year = data.get("year", 0)
                series.tvdb_id = data.get("tvdbId") or None
                series.imdb_id = data.get("imdbId")
                if data.get("tmdbId"):
                    series.tmdb_id = data.get("tmdbId")

                # Resolve tmdb_id if missing
                if series.tmdb_id is None and self.tmdb:
                    try:
                        resolved_tmdb_id = None
                        if series.tvdb_id:
                            find_res = await self.tmdb.find_by_external_id(str(series.tvdb_id), "tvdb_id")
                            if find_res and find_res.get("tv_results"):
                                resolved_tmdb_id = find_res["tv_results"][0]["id"]
                        elif series.imdb_id:
                            find_res = await self.tmdb.find_by_external_id(str(series.imdb_id), "imdb_id")
                            if find_res and find_res.get("tv_results"):
                                resolved_tmdb_id = find_res["tv_results"][0]["id"]
                        if resolved_tmdb_id:
                            series.tmdb_id = resolved_tmdb_id
                    except Exception as e:
                        logger.warning("Failed to resolve TMDB ID for series sonarr_id=%s: %s", sonarr_id, e)

                if previous_tmdb_id != series.tmdb_id:
                    series.director = None
                    series.production_companies_json = None
                    series.tagline = None

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

                # Fallback: recompute counts from Episode rows if statistics is missing/zero-file-count
                seasons = (await self.db.execute(select(Season).where(Season.series_id == series.id))).scalars().all()
                if any(s.episode_file_count == 0 for s in seasons):
                    episodes = (await self.db.execute(select(Episode).where(Episode.series_id == series.id))).scalars().all()
                    episodes_by_season = {}
                    for ep in episodes:
                        episodes_by_season.setdefault(ep.season_number, []).append(ep)
                    for s in seasons:
                        if s.episode_file_count == 0:
                            eps = episodes_by_season.get(s.season_number, [])
                            total_episodes = len(eps)
                            file_episodes = sum(1 for ep in eps if ep.episode_file_path is not None)
                            if file_episodes > 0:
                                s.episode_count = total_episodes
                                s.episode_file_count = file_episodes

                if _needs_tv_enrichment(series):
                    enrich_candidates.append(series)

            except Exception:
                logger.error("Error syncing series sonarr_id=%s", sonarr_id, exc_info=True)
                result.series.errors += 1

        if self.tmdb and enrich_candidates:
            await self._enrich_tv_from_tmdb(enrich_candidates)

        if result.series.errors + result.seasons.errors + result.episodes.errors == 0:
            observed_ids = {int(data["id"]) for data in raw_series}
            for sonarr_id, series in existing.items():
                if sonarr_id not in observed_ids and series.is_present:
                    series.is_present = False
                    series.retired_at = now
                    seasons = (
                        await self.db.execute(select(Season).where(Season.series_id == series.id))
                    ).scalars()
                    for season in seasons:
                        season.is_present = False
                        season.retired_at = now
                    episodes = (
                        await self.db.execute(select(Episode).where(Episode.series_id == series.id))
                    ).scalars()
                    for episode in episodes:
                        episode.is_present = False
                        episode.retired_at = now
                    media_files = (
                        await self.db.execute(
                            select(MediaFile)
                            .join(
                                EpisodeMediaFile,
                                EpisodeMediaFile.media_file_id == MediaFile.id,
                            )
                            .join(Episode, Episode.id == EpisodeMediaFile.episode_id)
                            .where(
                                Episode.series_id == series.id,
                                MediaFile.is_present.is_(True),
                            )
                            .distinct()
                        )
                    ).scalars()
                    for media_file in media_files:
                        media_file.is_present = False
                        media_file.is_active = False
                        media_file.retired_at = now

        await self.db.commit()
        return result

    async def _enrich_tv_from_tmdb(self, series_list: list[Series]) -> None:
        """Backfill OCR TV metadata from TMDB without blocking the sync."""
        semaphore = asyncio.Semaphore(5)

        async def enrich(series: Series) -> None:
            tmdb_id = series.tmdb_id
            if tmdb_id is None or not _needs_tv_enrichment(series):
                return
            try:
                async with semaphore:
                    details = await self.tmdb.get_tv_details(tmdb_id)
            except Exception:
                logger.warning(
                    "TMDB enrichment failed for series sonarr_id=%s tmdb_id=%s",
                    series.sonarr_id,
                    tmdb_id,
                    exc_info=True,
                )
                return

            series.director = details.director
            series.production_companies_json = details.production_companies
            series.tagline = details.tagline

        await asyncio.gather(*(enrich(s) for s in series_list))

    # ── Seasons ──────────────────────────────────────────────────────

    async def _sync_seasons(self, series: Series, sonarr_seasons: list[dict]) -> SyncResult:
        """Upsert season rows from the Sonarr series response.

        Sonarr includes a ``seasons`` array in the ``/api/v3/series``
        response — no extra API call needed.
        """
        result = SyncResult()
        now = datetime.now(UTC)

        # Index existing seasons by season_number
        existing_rows = (
            (await self.db.execute(select(Season).where(Season.series_id == series.id)))
            .scalars()
            .all()
        )
        existing = {s.season_number: s for s in existing_rows}

        for sdata in sonarr_seasons:
            season_num = sdata.get("seasonNumber", 0)
            # Skip invalid numbers
            if season_num < 0:
                continue

            season = existing.get(season_num)
            if season is None:
                season = Season(series_id=series.id, season_number=season_num)
                self.db.add(season)
                result.created += 1
            else:
                result.updated += 1

            season.is_present = True
            season.retired_at = None
            season.last_seen_at = now

            stats = sdata.get("statistics") or {}
            season.episode_count = int(stats.get("episodeCount") or 0)
            season.episode_file_count = int(stats.get("episodeFileCount") or 0)

            # tmdb_id for seasons is populated in Phase N
            await self._check_existing_poster(season, series=series)

        observed_numbers = {
            int(data.get("seasonNumber", 0))
            for data in sonarr_seasons
            if int(data.get("seasonNumber", 0)) >= 0
        }
        for season_number, season in existing.items():
            if season_number not in observed_numbers and season.is_present:
                season.is_present = False
                season.retired_at = now

        return result

    # ── Episodes ─────────────────────────────────────────────────────

    async def _sync_episodes(self, series: Series) -> SyncResult:
        """Sync episode rows for a single series from Sonarr.

        Sonarr has no "all episodes" endpoint — we must query per-series.
        For a personal library (~10-100 series), the N API calls are
        acceptable.  Episode file paths come from the episode-file join.
        """
        result = SyncResult()
        now = datetime.now(UTC)

        raw_episodes = await self.sonarr.get_episodes(series.sonarr_id)
        raw_files = await self.sonarr.get_episode_files(series.sonarr_id)
        if not isinstance(raw_episodes, list) or not isinstance(raw_files, list):
            raise TypeError("Sonarr episode responses must be complete lists")

        # Build episode-file lookup
        file_by_id: dict[int, dict] = {f["id"]: f for f in raw_files}

        # Index existing episodes by sonarr_episode_id
        existing_rows = (
            (await self.db.execute(select(Episode).where(Episode.series_id == series.id)))
            .scalars()
            .all()
        )
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

            episode.is_present = True
            episode.retired_at = None
            episode.last_seen_at = now

            episode.season_number = edata.get("seasonNumber", 0)
            episode.episode_number = edata.get("episodeNumber", 0)
            episode.title = edata.get("title")

            # Resolve file path from episode file
            file_id = edata.get("episodeFileId")
            fdata = file_by_id.get(file_id) if file_id else None
            if fdata is not None:
                episode.episode_file_path = fdata.get("path")

                # ── HDR / Dolby Vision + resolution (plan 06) ──────────
                hdr_type_raw, has_hdr, has_dv = _extract_hdr(fdata)
                episode.hdr_type_raw = hdr_type_raw
                episode.has_hdr = has_hdr
                episode.has_dv = has_dv
                width, height, _ = _extract_media_info(fdata)
                episode.video_width = width
                episode.video_height = height
                episode.audio_languages_json = _extract_language_list(fdata, "audioLanguages")
                episode.subtitle_languages_json = _extract_language_list(fdata, "subtitles")
            else:
                # File was deleted — HDR/resolution truth is no longer known.
                episode.hdr_type_raw = None
                episode.has_hdr = None
                episode.has_dv = None
                episode.video_width = None
                episode.video_height = None
                episode.audio_languages_json = None
                episode.subtitle_languages_json = None

        observed_episode_ids = {int(data["id"]) for data in raw_episodes}
        for sonarr_episode_id, episode in existing.items():
            if sonarr_episode_id not in observed_episode_ids and episode.is_present:
                episode.is_present = False
                episode.retired_at = now

        # ── Physical media-file rows + episode associations (§19.3) ──
        await self.db.flush()  # assign episode.id for new rows
        await _upsert_episode_media_files(self.db, series.id, raw_episodes, file_by_id)

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
        existing = (
            await db.execute(
                select(MediaFile).where(
                    MediaFile.movie_id == movie.id,
                    MediaFile.is_present.is_(True),
                )
            )
        ).scalars()
        now = datetime.now(UTC)
        for media_file in existing:
            media_file.is_present = False
            media_file.is_active = False
            media_file.retired_at = now
        return

    source_key = f"radarr:movie-file:{file_id}" if file_id else f"radarr:movie:{movie.id}"
    container = Path(path).suffix.lstrip(".").lower() or None
    size = movie_file.get("size")

    existing = (
        (
            await db.execute(
                select(MediaFile).where(
                    MediaFile.movie_id == movie.id, MediaFile.is_active.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )
    previous_active_ids = {row.id for row in existing if row.id is not None}
    by_source_key = (
        await db.execute(select(MediaFile).where(MediaFile.source_key == source_key))
    ).scalar_one_or_none()

    current = by_source_key or next((m for m in existing if m.path == path), None)
    for other in existing:
        if other is not current:
            other.is_active = False  # replaced/old file → keep for history
            other.is_present = False
            other.retired_at = datetime.now(UTC)

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
    current.is_present = True
    current.retired_at = None
    current.last_seen_at = datetime.now(UTC)
    await db.flush()
    if previous_active_ids and current.id not in previous_active_ids:
        await _reset_letterbox_for_media_replacement(
            db,
            media_type="movie",
            movie_id=movie.id,
            current_media_file=current,
        )


async def _upsert_episode_media_files(
    db: AsyncSession, series_id: int, raw_episodes: list[dict], file_by_id: dict[int, dict]
) -> None:
    """Upsert one MediaFile per Sonarr episode-file and associate every Episode.

    Correctly represents multi-episode files (many Episode rows → one file).
    """
    # Map each episode-file id to the set of Episode rows that reference it.
    eps_by_file: dict[int, list[Episode]] = {}
    previous_media_file_by_episode: dict[int, int] = {}
    displaced_media_file_ids: set[int] = set()
    episode_ids = [int(edata["id"]) for edata in raw_episodes if edata.get("id") is not None]
    if episode_ids:
        previous_rows = (
            await db.execute(
                select(EpisodeMediaFile.episode_id, MediaFile.id)
                .join(MediaFile, MediaFile.id == EpisodeMediaFile.media_file_id)
                .join(Episode, Episode.id == EpisodeMediaFile.episode_id)
                .where(
                    Episode.sonarr_episode_id.in_(episode_ids),
                    MediaFile.is_active.is_(True),
                )
                .order_by(EpisodeMediaFile.episode_id, MediaFile.id)
            )
        ).all()
        for episode_id, media_file_id in previous_rows:
            previous_media_file_by_episode.setdefault(episode_id, media_file_id)

    for edata in raw_episodes:
        file_id = edata.get("episodeFileId")
        if not file_id or file_id not in file_by_id:
            continue
        ep = (
            await db.execute(select(Episode).where(Episode.sonarr_episode_id == edata["id"]))
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
        media_file.is_present = True
        media_file.retired_at = None
        media_file.last_seen_at = datetime.now(UTC)
        await db.flush()  # assign media_file.id

        for ep in episodes:
            stale_links = (
                await db.execute(
                    select(EpisodeMediaFile).where(
                        EpisodeMediaFile.episode_id == ep.id,
                        EpisodeMediaFile.media_file_id != media_file.id,
                    )
                )
            ).scalars().all()
            for stale_link in stale_links:
                displaced_media_file_ids.add(stale_link.media_file_id)
                await db.delete(stale_link)

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
                db.add(EpisodeMediaFile(episode_id=ep.id, media_file_id=media_file.id))

            previous_media_file_id = previous_media_file_by_episode.get(ep.id)
            if previous_media_file_id is not None and previous_media_file_id != media_file.id:
                await _reset_letterbox_for_media_replacement(
                    db,
                    media_type="episode",
                    episode_id=ep.id,
                    current_media_file=media_file,
                )

    if displaced_media_file_ids:
        await db.flush()
        for media_file_id in displaced_media_file_ids:
            remaining_link = (
                await db.execute(
                    select(EpisodeMediaFile.media_file_id).where(
                        EpisodeMediaFile.media_file_id == media_file_id
                    )
                )
            ).scalar_one_or_none()
            if remaining_link is None:
                displaced = await db.get(MediaFile, media_file_id)
                if displaced is not None:
                    displaced.is_active = False
                    displaced.is_present = False
                    displaced.retired_at = datetime.now(UTC)

    expected_source_keys = {f"sonarr:episode-file:{file_id}" for file_id in eps_by_file}
    stale_query = (
        select(MediaFile)
        .join(EpisodeMediaFile, EpisodeMediaFile.media_file_id == MediaFile.id)
        .join(Episode, Episode.id == EpisodeMediaFile.episode_id)
        .where(
            MediaFile.source == "sonarr",
            MediaFile.is_active.is_(True),
            Episode.series_id == series_id,
        )
        .distinct()
    )
    if expected_source_keys:
        stale_query = stale_query.where(MediaFile.source_key.notin_(expected_source_keys))
    stale_media_rows = (await db.execute(stale_query)).scalars().all()
    for media_row in stale_media_rows:
        media_row.is_active = False
        media_row.is_present = False
        media_row.retired_at = datetime.now(UTC)


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


def _extract_language_list(media_file: dict, key: str) -> list[str] | None:
    """Normalize Sonarr ``mediaInfo`` language strings into ordered unique tags.

    ``None`` means the file has no ``mediaInfo`` payload at all, so sync learned
    nothing about that dimension. ``[]`` means ``mediaInfo`` existed but Sonarr
    reported no languages for the requested field.
    """
    media_info = media_file.get("mediaInfo")
    if media_info is None or not isinstance(media_info, dict):
        return None

    raw = media_info.get(key)
    if raw is None:
        return []

    values = raw if isinstance(raw, list) else str(raw).split("/")
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = str(value).strip()
        if not token:
            continue
        tag, _ = subtitle_languages.normalize(token)
        if tag not in seen:
            normalized.append(tag)
            seen.add(tag)
    return normalized


def _extract_hdr(movie_file: dict) -> tuple[str | None, bool | None, bool | None]:
    """Derive raw HDR truth plus legacy ``(has_hdr, has_dv)`` flags.

    Radarr exposes ``videoDynamicRangeType`` (e.g. ``"DV"``, ``"HDR10"``,
    ``"HDR10Plus"``, ``"HLG"``, ``"PQ"``, ``"DV HDR10"``) and/or
    ``videoDynamicRange`` (``"HDR"``/``"SDR"``/``""``). The raw value prefers
    ``videoDynamicRangeType`` and falls back to ``videoDynamicRange``.
    """
    media_info = movie_file.get("mediaInfo") or {}
    hdr_type_raw = (
        (media_info.get("videoDynamicRangeType") or "").strip()
        or (media_info.get("videoDynamicRange") or "").strip()
        or None
    )
    # Radarr only populates videoDynamicRangeType for HDR content.
    # When mediaInfo is present (file was analyzed) but no dynamic-range
    # field was reported, the file is SDR — not unknown.
    if hdr_type_raw is None and media_info:
        has_hdr, has_dv = False, False
        return "SDR", has_hdr, has_dv
    has_hdr, has_dv = classify_hdr_flags(hdr_type_raw)
    return hdr_type_raw, has_hdr, has_dv


async def _sync_radarr_overlay_reference_data(
    db: AsyncSession,
    custom_formats: list[dict],
    quality_profiles: list[dict],
    synced_at: datetime,
) -> tuple[dict[int, str], dict[int, dict[int, int]]]:
    """Upsert Radarr custom-format and quality-profile metadata."""
    existing_custom_formats = {
        row.id: row for row in (await db.execute(select(RadarrCustomFormat))).scalars()
    }
    existing_profiles = {
        row.id: row for row in (await db.execute(select(RadarrQualityProfile))).scalars()
    }

    custom_format_names: dict[int, str] = {}
    for payload in custom_formats:
        cf_id = payload.get("id")
        if cf_id is None:
            continue
        row = existing_custom_formats.get(cf_id)
        if row is None:
            row = RadarrCustomFormat(id=cf_id)
            db.add(row)
        row.name = payload.get("name") or f"Custom Format {cf_id}"
        row.include_when_renaming = bool(payload.get("includeCustomFormatWhenRenaming"))
        row.specifications_json = payload.get("specifications")
        row.synced_at = synced_at
        custom_format_names[cf_id] = row.name

    for payload in quality_profiles:
        profile_id = payload.get("id")
        if profile_id is None:
            continue
        row = existing_profiles.get(profile_id)
        if row is None:
            row = RadarrQualityProfile(id=profile_id)
            db.add(row)
        row.name = payload.get("name") or f"Profile {profile_id}"
        row.upgrade_allowed = payload.get("upgradeAllowed")
        row.cutoff_format_score = payload.get("cutoffFormatScore")
        row.min_format_score = payload.get("minFormatScore")
        row.synced_at = synced_at

        for item in payload.get("formatItems") or []:
            cf_id = item.get("format")
            if cf_id is None or cf_id in custom_format_names:
                continue
            placeholder = existing_custom_formats.get(cf_id)
            if placeholder is None:
                placeholder = RadarrCustomFormat(id=cf_id)
                db.add(placeholder)
                existing_custom_formats[cf_id] = placeholder
            placeholder.name = item.get("name") or f"Custom Format {cf_id}"
            placeholder.include_when_renaming = False
            placeholder.specifications_json = None
            placeholder.synced_at = synced_at
            custom_format_names[cf_id] = placeholder.name

    custom_format_ids = {
        payload.get("id") for payload in custom_formats if payload.get("id") is not None
    }
    profile_ids = {
        payload.get("id") for payload in quality_profiles if payload.get("id") is not None
    }

    if custom_format_ids:
        await db.execute(
            delete(RadarrCustomFormat).where(RadarrCustomFormat.id.not_in(custom_format_ids))
        )
    if profile_ids:
        await db.execute(
            delete(RadarrQualityProfile).where(RadarrQualityProfile.id.not_in(profile_ids))
        )

    await db.execute(delete(RadarrProfileFormatItem))
    profile_scores_by_profile: dict[int, dict[int, int]] = {}
    for payload in quality_profiles:
        profile_id = payload.get("id")
        if profile_id is None:
            continue
        profile_scores: dict[int, int] = {}
        for item in payload.get("formatItems") or []:
            cf_id = item.get("format")
            score = item.get("score")
            if cf_id is None or score is None:
                continue
            score_int = int(score)
            db.add(
                RadarrProfileFormatItem(
                    profile_id=profile_id,
                    custom_format_id=cf_id,
                    score=score_int,
                )
            )
            profile_scores[cf_id] = score_int
        profile_scores_by_profile[profile_id] = profile_scores

    return custom_format_names, profile_scores_by_profile


async def _sync_sonarr_overlay_reference_data(
    db: AsyncSession,
    custom_formats: list[dict],
    quality_profiles: list[dict],
    synced_at: datetime,
) -> tuple[dict[int, str], dict[int, dict[int, int]]]:
    """Upsert Sonarr custom-format and quality-profile metadata.

    Mirrors ``_sync_radarr_overlay_reference_data``. No per-episode
    custom-format score capture (H6) — the returned score maps are unused,
    but kept for symmetry with the Radarr helper.
    """
    existing_custom_formats = {
        row.id: row for row in (await db.execute(select(SonarrCustomFormat))).scalars()
    }
    existing_profiles = {
        row.id: row for row in (await db.execute(select(SonarrQualityProfile))).scalars()
    }

    custom_format_names: dict[int, str] = {}
    for payload in custom_formats:
        cf_id = payload.get("id")
        if cf_id is None:
            continue
        row = existing_custom_formats.get(cf_id)
        if row is None:
            row = SonarrCustomFormat(id=cf_id)
            db.add(row)
        row.name = payload.get("name") or f"Custom Format {cf_id}"
        row.include_when_renaming = bool(payload.get("includeCustomFormatWhenRenaming"))
        row.specifications_json = payload.get("specifications")
        row.synced_at = synced_at
        custom_format_names[cf_id] = row.name

    for payload in quality_profiles:
        profile_id = payload.get("id")
        if profile_id is None:
            continue
        row = existing_profiles.get(profile_id)
        if row is None:
            row = SonarrQualityProfile(id=profile_id)
            db.add(row)
        row.name = payload.get("name") or f"Profile {profile_id}"
        row.upgrade_allowed = payload.get("upgradeAllowed")
        row.cutoff_format_score = payload.get("cutoffFormatScore")
        row.min_format_score = payload.get("minFormatScore")
        row.synced_at = synced_at

        for item in payload.get("formatItems") or []:
            cf_id = item.get("format")
            if cf_id is None or cf_id in custom_format_names:
                continue
            placeholder = existing_custom_formats.get(cf_id)
            if placeholder is None:
                placeholder = SonarrCustomFormat(id=cf_id)
                db.add(placeholder)
                existing_custom_formats[cf_id] = placeholder
            placeholder.name = item.get("name") or f"Custom Format {cf_id}"
            placeholder.include_when_renaming = False
            placeholder.specifications_json = None
            placeholder.synced_at = synced_at
            custom_format_names[cf_id] = placeholder.name

    custom_format_ids = {
        payload.get("id") for payload in custom_formats if payload.get("id") is not None
    }
    profile_ids = {
        payload.get("id") for payload in quality_profiles if payload.get("id") is not None
    }

    if custom_format_ids:
        await db.execute(
            delete(SonarrCustomFormat).where(SonarrCustomFormat.id.not_in(custom_format_ids))
        )
    if profile_ids:
        await db.execute(
            delete(SonarrQualityProfile).where(SonarrQualityProfile.id.not_in(profile_ids))
        )

    await db.execute(delete(SonarrProfileFormatItem))
    profile_scores_by_profile: dict[int, dict[int, int]] = {}
    for payload in quality_profiles:
        profile_id = payload.get("id")
        if profile_id is None:
            continue
        profile_scores: dict[int, int] = {}
        for item in payload.get("formatItems") or []:
            cf_id = item.get("format")
            score = item.get("score")
            if cf_id is None or score is None:
                continue
            score_int = int(score)
            db.add(
                SonarrProfileFormatItem(
                    profile_id=profile_id,
                    custom_format_id=cf_id,
                    score=score_int,
                )
            )
            profile_scores[cf_id] = score_int
        profile_scores_by_profile[profile_id] = profile_scores

    return custom_format_names, profile_scores_by_profile


async def _replace_movie_custom_format_scores(
    db: AsyncSession,
    movie_id: int,
    custom_formats: list[dict],
    custom_format_names: dict[int, str],
    profile_scores_by_cf: dict[int, int],
    synced_at: datetime,
) -> None:
    """Replace the current-file custom-format scores for one movie."""
    await db.execute(
        delete(MovieCustomFormatScore).where(MovieCustomFormatScore.movie_id == movie_id)
    )

    for payload in custom_formats:
        cf_id = payload.get("id")
        if cf_id is None:
            continue
        if cf_id not in custom_format_names:
            db.add(
                RadarrCustomFormat(
                    id=cf_id,
                    name=payload.get("name") or f"Custom Format {cf_id}",
                    include_when_renaming=False,
                    specifications_json=None,
                    synced_at=synced_at,
                )
            )
            custom_format_names[cf_id] = payload.get("name") or f"Custom Format {cf_id}"
        db.add(
            MovieCustomFormatScore(
                movie_id=movie_id,
                custom_format_id=cf_id,
                score=int(profile_scores_by_cf.get(cf_id, 0)),
                synced_at=synced_at,
            )
        )


@lru_cache(maxsize=1000)
def _validate_folder_cached(raw_path: str, source: str) -> Path | None:
    """Translate and validate a folder path from an *arr API (cached).

    LRU cache (1000 entries) prevents redundant filesystem hits during sync.
    On NFS/CIFS mounts, each validation is 10-50ms; caching reduces 1000-movie
    sync from 50s → 1-5s. Cache is cleared after each sync to avoid stale data.

    Returns the resolved ``Path``, or ``None`` if validation fails.
    """
    try:
        return safe_translate_and_validate(raw_path, source=source)
    except ValueError:
        logger.warning("Path validation failed for: %s (source=%s)", raw_path, source)
        return None


def _validate_folder(raw_path: str, *, source: str = "radarr") -> Path | None:
    """Wrapper around cached validation (signature matches original call sites)."""
    return _validate_folder_cached(raw_path, source)


def _build_movie_poster_filename(movie: Movie) -> str:
    """Resolve the movie poster filename from the configured format."""
    fmt = settings.MOVIE_POSTER_FORMAT

    if "{movie_basename}" in fmt and movie.movie_file_path:
        basename = Path(movie.movie_file_path).stem
        return fmt.replace("{movie_basename}", basename)

    return fmt
