"""Library responses. None of them carries a local path or Radarr's path."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from marquee.library.queries import MovieWithPoster


class Poster(BaseModel):
    file_name: str = Field(description="The file's name inside the movie folder")
    sha256: str
    size_bytes: int
    url: str = Field(description="Where to fetch the image, with the API key")


class MovieItem(BaseModel):
    id: int
    radarr_id: int
    tmdb_id: int | None
    imdb_id: str | None
    title: str
    sort_title: str
    year: int | None
    genres: list[str]
    has_file: bool = Field(description="Radarr has the movie's file; false for wanted movies")
    file_size_bytes: int | None
    added_at: datetime | None = Field(description="When Radarr added the movie")
    director: str | None
    production_companies: list[str] | None
    tagline: str | None
    poster: Poster | None

    @classmethod
    def build(cls, row: MovieWithPoster) -> MovieItem:
        movie, artwork = row.movie, row.poster
        poster = (
            Poster(
                file_name=artwork.file_name,
                sha256=artwork.sha256,
                size_bytes=artwork.size_bytes,
                url=f"/api/movies/{movie.id}/poster",
            )
            if artwork is not None
            else None
        )
        return cls(
            id=movie.id,
            radarr_id=movie.radarr_id,
            tmdb_id=movie.tmdb_id,
            imdb_id=movie.imdb_id,
            title=movie.title,
            sort_title=movie.sort_title,
            year=movie.year,
            genres=list(movie.genres or []),
            has_file=movie.has_file,
            file_size_bytes=movie.file_size_bytes,
            added_at=movie.radarr_added_at,
            director=movie.director,
            production_companies=movie.production_companies,
            tagline=movie.tagline,
            poster=poster,
        )


class MovieList(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[MovieItem]
