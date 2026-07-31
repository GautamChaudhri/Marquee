"""Immutable versioned subject snapshots for canonical jobs."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, TypeAdapter, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.jobs.documents import PosterPipelineSubjectKey, StrictDocument
from marquee.core.jobs.poster_group_limits import MAX_POSTER_GROUP_MEMBERS
from marquee.models import (
    Episode,
    EpisodeMediaFile,
    MediaFile,
    Movie,
    Season,
    Series,
)


def _display_file(path: str | None) -> str | None:
    if not path:
        return None
    return PurePosixPath(path.replace("\\", "/")).name or None


class SubjectNotFoundError(LookupError):
    pass


class SubjectSnapshotBase(StrictDocument):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1] = 1
    display_id: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=500)
    snapshot_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MovieSnapshot(SubjectSnapshotBase):
    kind: Literal["movie"] = "movie"
    movie_id: int
    title: str
    year: int | None = None
    radarr_id: int | None = None
    tmdb_id: int | None = None
    imdb_id: str | None = None
    file_name: str | None = None
    media_kind: Literal["movie"] = "movie"
    artwork_key: str | None = None


class SeriesSnapshot(SubjectSnapshotBase):
    kind: Literal["series"] = "series"
    series_id: int
    series_title: str
    year: int | None = None
    sonarr_id: int | None = None
    tvdb_id: int | None = None
    tmdb_id: int | None = None
    artwork_key: str | None = None


class SeasonSnapshot(SubjectSnapshotBase):
    kind: Literal["season"] = "season"
    season_id: int
    series_id: int
    series_title: str
    season_number: int
    year: int | None = None
    artwork_key: str | None = None


PosterSubjectSnapshot = Annotated[
    MovieSnapshot | SeriesSnapshot | SeasonSnapshot,
    Field(discriminator="kind"),
]


def poster_snapshot_subject_key(subject: PosterSubjectSnapshot) -> str:
    """Return the canonical durable identity for a poster-capable snapshot."""

    if isinstance(subject, MovieSnapshot):
        return f"movie:{subject.movie_id}"
    if isinstance(subject, SeriesSnapshot):
        return f"series:{subject.series_id}"
    return f"season:{subject.season_id}"


class PosterSubjectGroupMemberSnapshot(StrictDocument):
    """One explicitly keyed live subject inside a bounded poster group."""

    subject_key: PosterPipelineSubjectKey
    subject: PosterSubjectSnapshot

    @model_validator(mode="after")
    def require_canonical_key(self) -> PosterSubjectGroupMemberSnapshot:
        if self.subject_key != poster_snapshot_subject_key(self.subject):
            raise ValueError("poster group member subject_key does not match its snapshot")
        return self


class PosterSubjectGroupSnapshot(SubjectSnapshotBase):
    kind: Literal["poster_subject_group"] = "poster_subject_group"
    library: Literal["movies", "tv"]
    chunk_index: int = Field(ge=0)
    # Mirrors PosterPipelineGroupRequestV1.chunk_total; see the note there.
    chunk_total: int | None = Field(default=None, ge=1)
    batch_mode: Literal["chunked", "all_at_once"] = "chunked"
    members: tuple[PosterSubjectGroupMemberSnapshot, ...] = Field(
        min_length=1, max_length=MAX_POSTER_GROUP_MEMBERS
    )

    @model_validator(mode="after")
    def require_one_library_and_unique_members(self) -> PosterSubjectGroupSnapshot:
        if self.chunk_total is not None and self.chunk_index >= self.chunk_total:
            raise ValueError("poster group snapshot chunk_index must fall inside chunk_total")
        keys = tuple(member.subject_key for member in self.members)
        if len(set(keys)) != len(keys):
            raise ValueError("poster group snapshot members must be unique")
        if any(
            (member.subject.kind == "movie") != (self.library == "movies")
            for member in self.members
        ):
            raise ValueError("poster group snapshot members must belong to its library")
        return self


class EpisodeSnapshot(SubjectSnapshotBase):
    kind: Literal["episode"] = "episode"
    episode_id: int
    series_id: int
    series_title: str
    season_number: int
    episode_number: int
    episode_code: str
    episode_title: str | None = None
    sonarr_episode_id: int | None = None
    file_name: str | None = None
    media_file_id: int | None = None
    artwork_key: str | None = None


class MediaFileSnapshot(SubjectSnapshotBase):
    kind: Literal["media_file"] = "media_file"
    media_file_id: int
    file_name: str
    media_kind: Literal["movie", "episode", "standalone"]
    source: str
    source_key: str
    movie_id: int | None = None
    movie_title: str | None = None
    series_id: int | None = None
    series_title: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    episode_title: str | None = None
    size_bytes: int | None = None
    container: str | None = None
    artwork_key: str | None = None


class PosterCandidateSetSnapshot(SubjectSnapshotBase):
    kind: Literal["poster_candidate_set"] = "poster_candidate_set"
    media_kind: Literal["movie", "series", "season"]
    subject_id: int
    title: str
    year: int | None = None
    season_number: int | None = None
    source_names: tuple[str, ...] = ()
    candidate_count: int | None = None
    artwork_key: str | None = None


class ModelProfileTrainingSnapshot(SubjectSnapshotBase):
    kind: Literal["model_profile_training"] = "model_profile_training"
    subject_type: Literal["model", "profile", "training"]
    name: str
    model_name: str | None = None
    profile_scope: str | None = None
    dataset_label: str | None = None


class AggregateBatchSnapshot(SubjectSnapshotBase):
    kind: Literal["aggregate_batch"] = "aggregate_batch"
    batch_type: str
    child_count: int | None = None
    sealed: bool = False
    scope_summary: str | None = Field(default=None, max_length=500)


class MaintenanceScopeSnapshot(SubjectSnapshotBase):
    kind: Literal["maintenance_scope"] = "maintenance_scope"
    scope: str
    dry_run: bool = False


class SystemWorkSnapshot(SubjectSnapshotBase):
    kind: Literal["system_work"] = "system_work"
    work: str


SubjectSnapshot = Annotated[
    MovieSnapshot
    | SeriesSnapshot
    | SeasonSnapshot
    | PosterSubjectGroupSnapshot
    | EpisodeSnapshot
    | MediaFileSnapshot
    | PosterCandidateSetSnapshot
    | ModelProfileTrainingSnapshot
    | AggregateBatchSnapshot
    | MaintenanceScopeSnapshot
    | SystemWorkSnapshot,
    Field(discriminator="kind"),
]
SUBJECT_SNAPSHOT_ADAPTER = TypeAdapter(SubjectSnapshot)


def movie_snapshot(movie: Movie) -> MovieSnapshot:
    return MovieSnapshot(
        display_id=f"movie:{movie.id}",
        display_name=movie.title,
        movie_id=movie.id,
        title=movie.title,
        year=movie.year or None,
        radarr_id=movie.radarr_id,
        tmdb_id=movie.tmdb_id,
        imdb_id=movie.imdb_id,
        file_name=_display_file(movie.movie_file_path),
        artwork_key=f"movie:{movie.id}" if movie.poster_path else None,
    )


def series_snapshot(series: Series) -> SeriesSnapshot:
    return SeriesSnapshot(
        display_id=f"series:{series.id}",
        display_name=series.title,
        series_id=series.id,
        series_title=series.title,
        year=series.year or None,
        sonarr_id=series.sonarr_id,
        tvdb_id=series.tvdb_id,
        tmdb_id=series.tmdb_id,
        artwork_key=f"series:{series.id}" if series.poster_path else None,
    )


def season_snapshot(season: Season, series: Series) -> SeasonSnapshot:
    label = f"{series.title} · Season {season.season_number}"
    return SeasonSnapshot(
        display_id=f"season:{season.id}",
        display_name=label,
        season_id=season.id,
        series_id=series.id,
        series_title=series.title,
        season_number=season.season_number,
        year=series.year or None,
        artwork_key=f"season:{season.id}" if season.poster_path else None,
    )


def episode_snapshot(
    episode: Episode, series: Series, media_file: MediaFile | None = None
) -> EpisodeSnapshot:
    code = f"S{episode.season_number:02d}E{episode.episode_number:02d}"
    name = f"{series.title} · {code}"
    if episode.title:
        name = f"{name} — {episode.title}"
    return EpisodeSnapshot(
        display_id=f"episode:{episode.id}",
        display_name=name,
        episode_id=episode.id,
        series_id=series.id,
        series_title=series.title,
        season_number=episode.season_number,
        episode_number=episode.episode_number,
        episode_code=code,
        episode_title=episode.title,
        sonarr_episode_id=episode.sonarr_episode_id,
        file_name=_display_file(media_file.path if media_file else episode.episode_file_path),
        media_file_id=media_file.id if media_file else None,
        artwork_key=f"series:{series.id}" if series.poster_path else None,
    )


def media_file_snapshot(
    media_file: MediaFile,
    *,
    movie: Movie | None = None,
    series: Series | None = None,
    episode: Episode | None = None,
) -> MediaFileSnapshot:
    file_name = _display_file(media_file.path)
    if file_name is None:
        raise ValueError("media file snapshot requires a display filename")
    media_kind = "movie" if movie else "episode" if episode else "standalone"
    return MediaFileSnapshot(
        display_id=f"media_file:{media_file.id}",
        display_name=file_name,
        media_file_id=media_file.id,
        file_name=file_name,
        media_kind=media_kind,
        source=media_file.source,
        source_key=media_file.source_key,
        movie_id=movie.id if movie else None,
        movie_title=movie.title if movie else None,
        series_id=series.id if series else None,
        series_title=series.title if series else None,
        season_number=episode.season_number if episode else None,
        episode_number=episode.episode_number if episode else None,
        episode_title=episode.title if episode else None,
        size_bytes=media_file.size_bytes,
        container=media_file.container,
        artwork_key=(
            f"movie:{movie.id}"
            if movie and movie.poster_path
            else f"series:{series.id}"
            if series and series.poster_path
            else None
        ),
    )


async def build_movie_snapshot(session: AsyncSession, movie_id: int) -> MovieSnapshot:
    movie = await session.get(Movie, movie_id)
    if movie is None:
        raise SubjectNotFoundError(f"movie {movie_id} does not exist")
    return movie_snapshot(movie)


async def build_series_snapshot(session: AsyncSession, series_id: int) -> SeriesSnapshot:
    series = await session.get(Series, series_id)
    if series is None:
        raise SubjectNotFoundError(f"series {series_id} does not exist")
    return series_snapshot(series)


async def build_season_snapshot(session: AsyncSession, season_id: int) -> SeasonSnapshot:
    season = await session.get(Season, season_id)
    if season is None:
        raise SubjectNotFoundError(f"season {season_id} does not exist")
    series = await session.get(Series, season.series_id)
    if series is None:
        raise SubjectNotFoundError(f"series {season.series_id} does not exist")
    return season_snapshot(season, series)


async def build_episode_snapshot(session: AsyncSession, episode_id: int) -> EpisodeSnapshot:
    episode = await session.get(Episode, episode_id)
    if episode is None:
        raise SubjectNotFoundError(f"episode {episode_id} does not exist")
    series = await session.get(Series, episode.series_id)
    if series is None:
        raise SubjectNotFoundError(f"series {episode.series_id} does not exist")
    return episode_snapshot(episode, series)


async def build_media_file_snapshot(session: AsyncSession, media_file_id: int) -> MediaFileSnapshot:
    """Resolve one media file with its movie or episode/series context for the subject."""
    media_file = await session.get(MediaFile, media_file_id)
    if media_file is None:
        raise SubjectNotFoundError(f"media file {media_file_id} does not exist")
    movie: Movie | None = None
    series: Series | None = None
    episode: Episode | None = None
    if media_file.movie_id is not None:
        movie = await session.get(Movie, media_file.movie_id)
    else:
        link = await session.scalar(
            select(EpisodeMediaFile)
            .where(EpisodeMediaFile.media_file_id == media_file_id)
            .order_by(EpisodeMediaFile.episode_id)
            .limit(1)
        )
        if link is not None:
            episode = await session.get(Episode, link.episode_id)
            if episode is not None:
                series = await session.get(Series, episode.series_id)
    return media_file_snapshot(media_file, movie=movie, series=series, episode=episode)
