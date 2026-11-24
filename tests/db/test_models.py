"""The ORM models as declared; the migrated schema is checked in test_migrate.py."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, UniqueConstraint

from marquee.db.base import Base
from marquee.db.models import Artwork, Job, JobEvent, Movie

# taste/map.py and taste/profile_enrich.py query these by name.
TASTE_COLUMNS = {"id", "title", "year", "tmdb_id", "genres"}


def test_only_the_library_and_job_tables_exist() -> None:
    assert set(Base.metadata.tables) == {
        "movies",
        "artwork",
        "jobs",
        "job_events",
        "job_schedules",
    }


def test_taste_code_column_names_exist() -> None:
    assert set(Movie.__table__.columns.keys()) >= TASTE_COLUMNS


def test_no_column_holds_a_local_path() -> None:
    names = set(Movie.__table__.columns.keys()) | set(Artwork.__table__.columns.keys())
    assert "radarr_path" in names
    assert not {"folder_path", "local_path", "poster_path", "path"} & names


def test_every_timestamp_is_timezone_aware() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if column.name.endswith("_at"):
                assert isinstance(column.type, DateTime), column
                assert column.type.timezone, column


def test_constraints_follow_the_naming_convention() -> None:
    movies = Movie.__table__
    artwork = Artwork.__table__
    assert movies.primary_key.name == "pk_movies"
    assert artwork.primary_key.name == "pk_artwork"
    assert {c.name for c in movies.constraints if isinstance(c, UniqueConstraint)} == {
        "uq_movies_radarr_id"
    }
    assert {i.name for i in movies.indexes} == {"ix_movies_tmdb_id"}
    assert {c.name for c in artwork.constraints if isinstance(c, UniqueConstraint)} == {
        "uq_artwork_movie_id_artwork_kind"
    }
    assert {c.name for c in artwork.constraints if isinstance(c, ForeignKeyConstraint)} == {
        "fk_artwork_movie_id_movies"
    }
    assert {c.name for c in artwork.constraints if isinstance(c, CheckConstraint)} == {
        "ck_artwork_subject_kind",
        "ck_artwork_artwork_kind",
        "ck_artwork_origin",
        "ck_artwork_sha256",
        "ck_artwork_file_name",
        "ck_artwork_size_bytes",
    }


def test_tmdb_id_is_indexed_but_not_unique() -> None:
    column = Movie.__table__.c.tmdb_id
    assert column.index and not column.unique
    assert Movie.__table__.c.radarr_id.unique


def test_job_constraints_and_cascades() -> None:
    jobs = Job.__table__
    assert jobs.primary_key.name == "pk_jobs"
    assert {c.name for c in jobs.constraints if isinstance(c, CheckConstraint)} == {
        "ck_jobs_job_class",
        "ck_jobs_state",
    }
    assert {i.name for i in jobs.indexes} == {
        "ix_jobs_dedupe_key_active",
        "ix_jobs_claim",
        "ix_jobs_parent_id",
    }
    (parent,) = jobs.c.parent_id.foreign_keys
    assert parent.ondelete == "CASCADE"
    (owner,) = JobEvent.__table__.c.job_id.foreign_keys
    assert owner.ondelete == "CASCADE"


def test_artwork_cascades_with_its_movie() -> None:
    (foreign_key,) = Artwork.__table__.c.movie_id.foreign_keys
    assert foreign_key.ondelete == "CASCADE"


def test_repr_names_the_movie_and_never_a_path() -> None:
    movie = Movie(id=1, radarr_id=7, title="Dune", radarr_path="/data/movies/Dune (2021)")
    text = repr(movie)
    assert "Movie" in text
    assert "Dune" in text
    assert "/data/movies" not in text
