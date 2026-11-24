"""The migrations against a real PostgreSQL server (see tests/conftest.py)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, pool, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from marquee.db import migrate
from marquee.db.base import Base
from marquee.db.models import Artwork, Movie

pytestmark = pytest.mark.db

SHA = "a" * 64
TASTE_COLUMNS = {"id", "title", "year", "tmdb_id", "genres"}
JOB_TABLES = {"jobs", "job_events", "job_schedules"}


def _sync_engine(url: str):  # noqa: ANN202 - a sqlalchemy Engine
    return create_engine(url, poolclass=pool.NullPool)


def _tables(url: str) -> set[str]:
    engine = _sync_engine(url)
    try:
        with engine.connect() as conn:
            return set(inspect(conn).get_table_names())
    finally:
        engine.dispose()


def _now() -> datetime:
    return datetime.now(UTC)


def _movie(radarr_id: int = 1, **values: object) -> Movie:
    defaults: dict[str, object] = {
        "radarr_id": radarr_id,
        "title": f"Movie {radarr_id}",
        "sort_title": f"movie {radarr_id}",
        "radarr_path": f"/radarr/movies/Movie {radarr_id}",
        "has_file": True,
        "last_seen_at": _now(),
    }
    return Movie(**{**defaults, **values})


def _poster(movie_id: int, **values: object) -> Artwork:
    defaults: dict[str, object] = {
        "movie_id": movie_id,
        "artwork_kind": "poster",
        "file_name": "poster.jpg",
        "sha256": SHA,
        "size_bytes": 10,
        "file_modified_at": _now(),
        "origin": "library",
        "first_seen_at": _now(),
        "last_seen_at": _now(),
    }
    return Artwork(**{**defaults, **values})


# ── Applying and undoing the migration ────────────────────────────────────


def test_the_migration_applies_to_an_empty_database(empty_database_url: str) -> None:
    before = migrate.status(url=empty_database_url)
    assert before.current is None
    assert not before.at_head

    after = migrate.upgrade(url=empty_database_url)
    assert after.at_head
    assert after.current == migrate.head_revision() == "0002"
    assert _tables(empty_database_url) == {"alembic_version", "movies", "artwork", *JOB_TABLES}


def test_downgrade_then_upgrade_works(empty_database_url: str) -> None:
    migrate.upgrade(url=empty_database_url)
    down = migrate.downgrade("base", url=empty_database_url)
    assert down.current is None
    assert _tables(empty_database_url) == {"alembic_version"}

    up = migrate.upgrade(url=empty_database_url)
    assert up.at_head
    assert {"movies", "artwork", *JOB_TABLES} <= _tables(empty_database_url)


def test_the_jobs_migration_downgrades_to_the_baseline(empty_database_url: str) -> None:
    migrate.upgrade(url=empty_database_url)
    down = migrate.downgrade("0001", url=empty_database_url)
    assert down.current == "0001"
    assert _tables(empty_database_url) == {"alembic_version", "movies", "artwork"}

    assert migrate.upgrade(url=empty_database_url).at_head


def test_models_and_the_migrated_database_agree(database_url: str) -> None:
    engine = _sync_engine(database_url)
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(
                conn, opts={"compare_type": True, "compare_server_default": True}
            )
            assert compare_metadata(context, Base.metadata) == []
    finally:
        engine.dispose()


def _check_constraints(url: str, table: str) -> set[str]:
    engine = _sync_engine(url)
    try:
        with engine.connect() as conn:
            return set(
                conn.execute(
                    text(
                        "select conname from pg_constraint "
                        "where conrelid = cast(:table as regclass) and contype = 'c'"
                    ),
                    {"table": table},
                ).scalars()
            )
    finally:
        engine.dispose()


def test_check_constraints_are_in_the_database(database_url: str) -> None:
    # Autogenerate does not compare CHECK constraints, so look for them by name.
    assert _check_constraints(database_url, "artwork") == {
        "ck_artwork_subject_kind",
        "ck_artwork_artwork_kind",
        "ck_artwork_origin",
        "ck_artwork_sha256",
        "ck_artwork_file_name",
        "ck_artwork_size_bytes",
    }
    assert _check_constraints(database_url, "jobs") == {"ck_jobs_job_class", "ck_jobs_state"}
    assert _check_constraints(database_url, "job_events") == {"ck_job_events_type"}


def test_the_job_indexes_are_in_the_database(database_url: str) -> None:
    engine = _sync_engine(database_url)
    try:
        with engine.connect() as conn:
            indexes = dict(
                conn.execute(
                    text("select indexname, indexdef from pg_indexes where tablename = 'jobs'")
                ).all()
            )
    finally:
        engine.dispose()
    assert "UNIQUE" in indexes["ix_jobs_dedupe_key_active"]
    assert "WHERE (state = ANY" in indexes["ix_jobs_dedupe_key_active"]
    assert "priority DESC, id" in indexes["ix_jobs_claim"]
    assert "WHERE (state = 'queued'" in indexes["ix_jobs_claim"]
    assert "parent_id" in indexes["ix_jobs_parent_id"]


def test_the_taste_code_columns_exist(database_url: str) -> None:
    engine = _sync_engine(database_url)
    try:
        with engine.connect() as conn:
            columns = {column["name"] for column in inspect(conn).get_columns("movies")}
    finally:
        engine.dispose()
    assert columns >= TASTE_COLUMNS


def test_every_timestamp_column_is_timestamptz(database_url: str) -> None:
    engine = _sync_engine(database_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "select table_name, column_name, data_type from information_schema.columns "
                    "where table_schema = 'public' and column_name like '%\\_at'"
                )
            ).all()
    finally:
        engine.dispose()
    assert rows
    assert {row.data_type for row in rows} == {"timestamp with time zone"}


def test_the_next_revision_is_numbered_after_the_newest() -> None:
    assert migrate._next_revision_id() == "0003"


# ── Constraints ───────────────────────────────────────────────────────────


async def _flush_fails(session: AsyncSession, *rows: object) -> None:
    session.add_all(rows)
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_defaults_fill_in(db_session: AsyncSession) -> None:
    movie = _movie()
    db_session.add(movie)
    await db_session.flush()
    await db_session.refresh(movie)
    assert movie.genres == []
    assert movie.is_present is True
    assert movie.created_at.tzinfo is not None
    assert movie.updated_at.tzinfo is not None


async def test_radarr_id_is_unique(db_session: AsyncSession) -> None:
    await _flush_fails(db_session, _movie(1), _movie(1, title="Again"))


async def test_tmdb_id_is_not_unique(db_session: AsyncSession) -> None:
    db_session.add_all([_movie(1, tmdb_id=603), _movie(2, tmdb_id=603)])
    await db_session.flush()


@pytest.mark.parametrize(
    "values",
    [
        {"subject_kind": "series"},
        {"artwork_kind": "backdrop"},
        {"origin": "deployed"},
        {"sha256": "not-a-digest"},
        {"sha256": "A" * 64},
        {"file_name": "folder/poster.jpg"},
        {"file_name": ".."},
        {"file_name": ""},
        {"size_bytes": -1},
    ],
)
async def test_artwork_checks_hold(db_session: AsyncSession, values: dict[str, object]) -> None:
    movie = _movie()
    db_session.add(movie)
    await db_session.flush()
    await _flush_fails(db_session, _poster(movie.id, **values))


async def test_one_poster_per_movie(db_session: AsyncSession) -> None:
    movie = _movie()
    db_session.add(movie)
    await db_session.flush()
    await _flush_fails(db_session, _poster(movie.id), _poster(movie.id, file_name="other.jpg"))


async def test_artwork_needs_its_movie(db_session: AsyncSession) -> None:
    await _flush_fails(db_session, _poster(9999))


async def test_artwork_goes_with_its_movie(
    db_engine: AsyncEngine, db_session: AsyncSession
) -> None:
    movie = _movie()
    db_session.add(movie)
    await db_session.flush()
    db_session.add(_poster(movie.id))
    await db_session.commit()

    async with db_engine.begin() as conn:
        await conn.execute(text("delete from movies"))
        remaining = (await conn.execute(text("select count(*) from artwork"))).scalar_one()
    assert remaining == 0
