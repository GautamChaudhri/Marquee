"""The ORM models. Importing this package registers every table on ``Base.metadata``."""

from __future__ import annotations

from marquee.db.models.artwork import ARTWORK_POSTER, ORIGIN_LIBRARY, SUBJECT_MOVIE, Artwork
from marquee.db.models.movie import Movie

__all__ = ["ARTWORK_POSTER", "ORIGIN_LIBRARY", "SUBJECT_MOVIE", "Artwork", "Movie"]
