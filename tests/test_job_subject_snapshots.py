from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from marquee.core.jobs.subjects import (
    SUBJECT_SNAPSHOT_ADAPTER,
    AggregateBatchSnapshot,
    MaintenanceScopeSnapshot,
    ModelProfileTrainingSnapshot,
    MovieSnapshot,
    PosterCandidateSetSnapshot,
    PosterSubjectGroupMemberSnapshot,
    PosterSubjectGroupSnapshot,
    SubjectNotFoundError,
    SystemWorkSnapshot,
    build_episode_snapshot,
    build_movie_snapshot,
    episode_snapshot,
    media_file_snapshot,
    movie_snapshot,
    season_snapshot,
    series_snapshot,
)
from marquee.models import Episode, MediaFile, Movie, Season, Series


async def test_media_snapshots_survive_live_subject_deletion(db) -> None:
    movie = Movie(
        title="Arrival",
        year=2016,
        folder_path="/secret/library/Arrival",
        movie_file_path="/secret/library/Arrival/Arrival (2016).mkv",
        radarr_id=11,
    )
    series = Series(title="The Expanse", year=2015, series_path="/secret/tv/The Expanse")
    db.add_all([movie, series])
    await db.flush()
    season = Season(series_id=series.id, season_number=3)
    episode = Episode(
        series_id=series.id,
        season_number=3,
        episode_number=7,
        title="Delta-V",
        episode_file_path="/secret/tv/The Expanse/S03E07.mkv",
    )
    db.add_all([season, episode])
    await db.flush()

    documents = [
        movie_snapshot(movie),
        series_snapshot(series),
        season_snapshot(season, series),
        episode_snapshot(episode, series),
    ]
    frozen = [item.model_dump(mode="json") for item in documents]
    await db.delete(movie)
    await db.delete(series)
    await db.flush()

    restored = [SUBJECT_SNAPSHOT_ADAPTER.validate_python(item) for item in frozen]
    assert restored[0].display_name == "Arrival"
    assert restored[3].display_name == "The Expanse · S03E07 — Delta-V"
    assert all("/secret/" not in str(item) for item in frozen)


async def test_transaction_builders_fail_clearly_for_missing_subjects(db) -> None:
    with pytest.raises(SubjectNotFoundError, match="movie 999"):
        await build_movie_snapshot(db, 999)
    with pytest.raises(SubjectNotFoundError, match="episode 999"):
        await build_episode_snapshot(db, 999)


async def test_transaction_builders_capture_loaded_subjects(db) -> None:
    movie = Movie(
        title="Moon",
        year=2009,
        folder_path="/movies/Moon",
        movie_file_path=r"C:\Movies\Moon\Moon.mkv",
    )
    db.add(movie)
    await db.flush()
    snapshot = await build_movie_snapshot(db, movie.id)
    assert snapshot.file_name == "Moon.mkv"


def test_media_file_snapshot_sanitizes_paths() -> None:
    media = MediaFile(
        id=4,
        source="radarr",
        source_key="radarr:movie-file:4",
        path="/mnt/private/Movie.mkv",
        is_active=True,
    )
    snapshot = media_file_snapshot(media)
    assert snapshot.file_name == "Movie.mkv"
    assert "/mnt/private" not in snapshot.model_dump_json()


def test_non_media_snapshot_variants_are_bounded() -> None:
    now = datetime.now(UTC)
    variants = [
        PosterCandidateSetSnapshot(
            display_id="poster:movie:1",
            display_name="Arrival candidates",
            snapshot_at=now,
            media_kind="movie",
            subject_id=1,
            title="Arrival",
            source_names=("tmdb",),
            candidate_count=42,
        ),
        ModelProfileTrainingSnapshot(
            display_id="profile:movies",
            display_name="Movie taste profile",
            snapshot_at=now,
            subject_type="profile",
            name="Movie taste profile",
            profile_scope="movies",
        ),
        AggregateBatchSnapshot(
            display_id="batch:1",
            display_name="Poster batch",
            snapshot_at=now,
            batch_type="poster_pipeline_batch",
            child_count=20,
            sealed=True,
        ),
        MaintenanceScopeSnapshot(
            display_id="maintenance:retention",
            display_name="Job retention",
            snapshot_at=now,
            scope="job_retention",
            dry_run=True,
        ),
        SystemWorkSnapshot(
            display_id="system:noop",
            display_name="System no-op",
            snapshot_at=now,
            work="system_noop",
        ),
    ]
    assert {SUBJECT_SNAPSHOT_ADAPTER.validate_python(item).kind for item in variants} == {
        "poster_candidate_set",
        "model_profile_training",
        "aggregate_batch",
        "maintenance_scope",
        "system_work",
    }
    assert not hasattr(variants[2], "children")


def test_discriminator_and_extra_fields_are_strict() -> None:
    with pytest.raises(ValidationError):
        SUBJECT_SNAPSHOT_ADAPTER.validate_python(
            {"version": 1, "kind": "future_subject", "display_id": "x", "display_name": "x"}
        )
    with pytest.raises(ValidationError):
        SUBJECT_SNAPSHOT_ADAPTER.validate_python(
            {
                "version": 1,
                "kind": "system_work",
                "display_id": "system:x",
                "display_name": "x",
                "work": "x",
                "secret": "not allowed",
            }
        )


def test_poster_group_snapshot_keys_and_library_are_self_consistent() -> None:
    movie = MovieSnapshot(
        display_id="movie:7",
        display_name="Arrival",
        movie_id=7,
        title="Arrival",
    )
    group = PosterSubjectGroupSnapshot(
        display_id="poster-group:movies:test-000",
        display_name="Movie poster group 1",
        library="movies",
        chunk_index=0,
        members=(PosterSubjectGroupMemberSnapshot(subject_key="movie:7", subject=movie),),
    )

    restored = SUBJECT_SNAPSHOT_ADAPTER.validate_python(group.model_dump(mode="json"))
    assert restored.kind == "poster_subject_group"
    assert restored.members[0].subject_key == "movie:7"

    with pytest.raises(ValidationError, match="subject_key"):
        PosterSubjectGroupMemberSnapshot(subject_key="movie:8", subject=movie)
    with pytest.raises(ValidationError, match="library"):
        PosterSubjectGroupSnapshot(
            display_id="poster-group:tv:test-000",
            display_name="TV poster group 1",
            library="tv",
            chunk_index=0,
            members=(PosterSubjectGroupMemberSnapshot(subject_key="movie:7", subject=movie),),
        )
