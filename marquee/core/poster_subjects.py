from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from marquee.config import settings
from marquee.core.path_utils import PathValidationError
from marquee.models.movie import Movie
from marquee.models.season import Season
from marquee.models.series import Series

MEDIA_TYPE_MOVIE = "movie"
MEDIA_TYPE_SERIES = "series"
MEDIA_TYPE_SEASON = "season"


@dataclass(frozen=True)
class PosterSubject:
    media_type: str
    movie: Movie | None = None
    series: Series | None = None
    season: Season | None = None

    @classmethod
    def from_movie(cls, movie: Movie) -> PosterSubject:
        return cls(media_type=MEDIA_TYPE_MOVIE, movie=movie)

    @classmethod
    def from_series(cls, series: Series) -> PosterSubject:
        return cls(media_type=MEDIA_TYPE_SERIES, series=series)

    @classmethod
    def from_season(cls, season: Season, series: Series) -> PosterSubject:
        if series is None:
            raise ValueError("series must be provided when building a PosterSubject from a season")
        return cls(media_type=MEDIA_TYPE_SEASON, series=series, season=season)

    @property
    def entity(self) -> Any:
        if self.media_type == MEDIA_TYPE_MOVIE:
            return self.movie
        elif self.media_type == MEDIA_TYPE_SERIES:
            return self.series
        elif self.media_type == MEDIA_TYPE_SEASON:
            return self.season
        raise ValueError(f"Unknown media type: {self.media_type}")

    @property
    def id(self) -> int:
        return self.entity.id

    @property
    def title(self) -> str:
        if self.media_type == MEDIA_TYPE_MOVIE:
            return self.movie.title
        elif self.media_type == MEDIA_TYPE_SERIES:
            return self.series.title
        elif self.media_type == MEDIA_TYPE_SEASON:
            return f"{self.series.title} - Season {self.season.season_number:02d}"
        raise ValueError(f"Unknown media type: {self.media_type}")

    @property
    def tmdb_id(self) -> int | None:
        if self.media_type == MEDIA_TYPE_MOVIE:
            return self.movie.tmdb_id
        elif self.media_type in (MEDIA_TYPE_SERIES, MEDIA_TYPE_SEASON):
            return self.series.tmdb_id
        raise ValueError(f"Unknown media type: {self.media_type}")

    @property
    def folder_raw(self) -> str | None:
        if self.media_type == MEDIA_TYPE_MOVIE:
            return self.movie.folder_path
        elif self.media_type in (MEDIA_TYPE_SERIES, MEDIA_TYPE_SEASON):
            return self.series.series_path
        raise ValueError(f"Unknown media type: {self.media_type}")

    @property
    def path_source(self) -> str:
        if self.media_type == MEDIA_TYPE_MOVIE:
            return "radarr"
        elif self.media_type in (MEDIA_TYPE_SERIES, MEDIA_TYPE_SEASON):
            return "sonarr"
        raise ValueError(f"Unknown media type: {self.media_type}")

    def render_filename(self) -> str:
        from marquee.core.poster_service import sanitize_poster_filename

        if self.media_type == MEDIA_TYPE_MOVIE:
            fmt = settings.MOVIE_POSTER_FORMAT
            if "{movie_basename}" in fmt:
                basename = Path(self.movie.movie_file_path).stem if self.movie.movie_file_path else "poster"
                try:
                    rendered = fmt.format(movie_basename=basename)
                except (KeyError, IndexError, ValueError) as exc:
                    raise PathValidationError(f"Invalid MOVIE_POSTER_FORMAT {fmt!r}: {exc}") from exc
            else:
                rendered = fmt
        elif self.media_type == MEDIA_TYPE_SERIES:
            rendered = settings.SERIES_POSTER_FORMAT
        elif self.media_type == MEDIA_TYPE_SEASON:
            fmt = settings.SEASON_POSTER_FORMAT
            try:
                rendered = fmt.format(season=self.season.season_number)
            except (KeyError, IndexError, ValueError) as exc:
                raise PathValidationError(f"Invalid SEASON_POSTER_FORMAT {fmt!r}: {exc}") from exc
        else:
            raise ValueError(f"Unknown media type: {self.media_type}")

        return sanitize_poster_filename(rendered)

    def cache_paths(self) -> tuple[Path, Path] | None:
        if self.tmdb_id is None:
            return None
        base_movies = settings.poster_cache_path / "movies"
        base_tv = settings.poster_cache_path / "tv"
        if self.media_type == MEDIA_TYPE_MOVIE:
            return base_movies / f"{self.tmdb_id}.jpg", base_movies / f"{self.tmdb_id}.meta.json"
        elif self.media_type == MEDIA_TYPE_SERIES:
            return base_tv / f"{self.tmdb_id}.jpg", base_tv / f"{self.tmdb_id}.meta.json"
        elif self.media_type == MEDIA_TYPE_SEASON:
            return base_tv / f"{self.tmdb_id}-s{self.season.season_number:02d}.jpg", base_tv / f"{self.tmdb_id}-s{self.season.season_number:02d}.meta.json"
        raise ValueError(f"Unknown media type: {self.media_type}")

    def backup_file(self) -> Path:
        if self.media_type == MEDIA_TYPE_MOVIE:
            if self.movie.poster_local_backup_path:
                return Path(self.movie.poster_local_backup_path)
            return settings.poster_backup_path / f"{self.movie.id}.jpg"
        elif self.media_type == MEDIA_TYPE_SERIES:
            if self.series.poster_local_backup_path:
                return Path(self.series.poster_local_backup_path)
            return settings.poster_backup_path / f"series-{self.id}.jpg"
        elif self.media_type == MEDIA_TYPE_SEASON:
            if self.season.poster_local_backup_path:
                return Path(self.season.poster_local_backup_path)
            return settings.poster_backup_path / f"season-{self.id}.jpg"
        raise ValueError(f"Unknown media type: {self.media_type}")

    def event_fk_kwargs(self) -> dict[str, Any]:
        return {
            "media_type": self.media_type,
            "movie_id": self.movie.id if self.movie else None,
            "series_id": self.series.id if self.series else None,
            "season_id": self.season.id if self.season else None,
        }

    def run_fk_kwargs(self) -> dict[str, Any]:
        return self.event_fk_kwargs()
