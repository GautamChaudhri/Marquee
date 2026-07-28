"""A3 sealed poster parents, bounded discovery, and aggregation contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pgqueuer import Queries
from sqlalchemy import func, select

import marquee.core.jobs.poster_parents as parent_module
from marquee.config import settings
from marquee.core.jobs.batches import cancel_batch_descendants, project_terminal_child
from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.poster_parents import (
    create_poster_parent,
    discover_poster_parent_subjects,
)
from marquee.database import _get_engine
from marquee.main import app
from marquee.models import Job, JobAttempt, JobBatch, JobDispatch, Movie, Season, Series


@pytest_asyncio.fixture(autouse=True)
async def installed_pgqueuer(db) -> Queries:
    async with _get_engine().connect() as connection:
        raw = await connection.get_raw_connection()
        queries = Queries.from_asyncpg_connection(raw.driver_connection)
        await queries.install()
        try:
            yield queries
        finally:
            await db.rollback()
            await queries.uninstall()


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as value:
        yield value


async def _subjects(db, root: Path) -> tuple[Movie, Series, Season]:
    movie_folder = root / "Movie"
    series_folder = root / "Series"
    movie_folder.mkdir(parents=True)
    series_folder.mkdir(parents=True)
    movie = Movie(
        title="Movie",
        year=2026,
        radarr_id=5101,
        folder_path=str(movie_folder),
        poster_path=str(movie_folder / "poster.jpg"),
    )
    series = Series(
        title="Series",
        sonarr_id=5102,
        series_path=str(series_folder),
        poster_path=str(series_folder / "poster.jpg"),
    )
    db.add_all([movie, series])
    await db.flush()
    season = Season(
        series_id=series.id,
        season_number=1,
        episode_file_count=1,
        poster_path=str(series_folder / "season01.jpg"),
    )
    db.add(season)
    await db.commit()
    return movie, series, season


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("parent_type", "child_type", "operation"),
    (
        ("poster_deploy_reset", "poster_reset", "reset"),
        ("poster_backup_all", "poster_backup_subject", "backup"),
    ),
)
async def test_reset_and_backup_are_sealed_ticketless_fixed_parents(
    db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    parent_type: str,
    child_type: str,
    operation: str,
) -> None:
    root = tmp_path / parent_type
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(root)])
    movie, series, season = await _subjects(db, root)

    async with db.begin():
        created = await create_poster_parent(
            db,
            parent_job_type=parent_type,
            idempotency_key=f"{parent_type}:sealed-test",
            trigger=TriggerKind.BATCH,
            initiator=None,
        )

    parent = await db.get(Job, created.parent.job_id)
    projection = await db.get(JobBatch, created.parent.job_id)
    children = tuple(
        (
            await db.scalars(
                select(Job).where(Job.parent_id == created.parent.job_id).order_by(Job.id)
            )
        ).all()
    )
    assert parent is not None
    assert (parent.pgq_job_id, parent.dispatch_generation, parent.current_attempt_id) == (
        None,
        0,
        None,
    )
    assert parent.request["operation"] == operation
    assert projection is not None
    assert (projection.mode, projection.sealed, projection.sealed_child_total) == (
        "fixed",
        True,
        3,
    )
    assert {child.type for child in children} == {child_type}
    assert {(child.subject_kind, int(child.subject_reference)) for child in children} == {
        ("movie", movie.id),
        ("series", series.id),
        ("season", season.id),
    }
    assert all(child.trigger_kind == "batch" and child.pgq_job_id is not None for child in children)
    assert (
        await db.scalar(select(func.count(JobDispatch.id)).where(JobDispatch.job_id == parent.id))
        == 0
    )
    assert (
        await db.scalar(select(func.count(JobAttempt.id)).where(JobAttempt.job_id == parent.id))
        == 0
    )


@pytest.mark.asyncio
async def test_heal_discovery_distinguishes_missing_unchanged_unsupported_and_grace(
    db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "heal"
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(root)])
    monkeypatch.setattr(settings, "HEAL_RECENT_DEPLOY_GRACE_MINUTES", 30)
    monkeypatch.setattr(settings, "SERIES_POSTER_FORMAT", "poster.jpg")
    monkeypatch.setattr(settings, "SEASON_POSTER_FORMAT", "season{season:02d}.jpg")
    movie, series, season = await _subjects(db, root)
    (root / "Movie" / "poster.jpg").write_bytes(b"present")
    (root / "Series" / "poster.jpg").write_bytes(b"present")
    unsupported = Movie(
        title="Unsupported",
        year=2026,
        radarr_id=5103,
        folder_path=str(tmp_path / "unsupported"),
        poster_path=str(tmp_path / "unsupported" / "poster.jpg"),
        poster_deployed_at=datetime.now(UTC) - timedelta(hours=1),
    )
    db.add(unsupported)
    season.poster_deployed_at = datetime.now(UTC)
    movie.poster_deployed_at = datetime.now(UTC) - timedelta(hours=1)
    series.poster_deployed_at = datetime.now(UTC) - timedelta(hours=1)
    await db.commit()

    discovered = await discover_poster_parent_subjects(db, operation="heal")
    assert discovered.subjects == ()
    assert discovered.unchanged_count == 2
    assert discovered.unsupported_count == 1

    season.poster_deployed_at = datetime.now(UTC) - timedelta(hours=1)
    await db.commit()
    discovered = await discover_poster_parent_subjects(db, operation="heal")
    assert discovered.subjects == (("season", season.id),)


@pytest.mark.asyncio
async def test_empty_parent_and_discovery_cap_are_bounded(
    db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "bounded"
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(root)])
    async with db.begin():
        empty = await create_poster_parent(
            db,
            parent_job_type="poster_deploy_reset",
            idempotency_key="poster_deploy_reset:empty-test",
            trigger=TriggerKind.BATCH,
            initiator=None,
        )
    parent = await db.get(Job, empty.parent.job_id)
    assert parent is not None
    assert (parent.phase, parent.outcome, parent.pgq_job_id) == (
        "terminal",
        "no_change",
        None,
    )

    await _subjects(db, root)
    monkeypatch.setattr(parent_module, "MAX_FIXED_CHILDREN", 2)
    with pytest.raises(ValueError, match="exceeds 2 subjects"):
        await discover_poster_parent_subjects(db, operation="backup")


@pytest.mark.asyncio
async def test_parent_itemizes_partial_failure_and_cancels_remaining_children(
    db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "aggregate"
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(root)])
    await _subjects(db, root)
    async with db.begin():
        created = await create_poster_parent(
            db,
            parent_job_type="poster_backup_all",
            idempotency_key="poster_backup_all:aggregate-test",
            trigger=TriggerKind.BATCH,
            initiator=None,
        )

    children = tuple(
        (
            await db.scalars(
                select(Job)
                .where(Job.parent_id == created.parent.job_id)
                .order_by(Job.created_at, Job.id)
            )
        ).all()
    )
    await db.commit()
    remaining_id = children[2].id
    async with db.begin():
        for child, outcome in zip(children[:2], ("succeeded", "failed"), strict=True):
            child.phase = "terminal"
            child.outcome = outcome
            child.terminal_at = datetime.now(UTC)
            await project_terminal_child(db, child)
        parent = await db.get(Job, created.parent.job_id)
        assert parent is not None
        cancelled = await cancel_batch_descendants(db, parent=parent)
        assert cancelled.terminal_total == 3

    db.expire_all()
    parent = await db.get(Job, created.parent.job_id)
    projection = await db.get(JobBatch, created.parent.job_id)
    assert parent is not None and parent.phase == "terminal"
    assert parent.outcome == "partially_succeeded"
    assert projection is not None
    assert projection.failure_summary["items"][0]["outcome"] == "failed"
    remaining = await db.get(Job, remaining_id)
    assert remaining is not None and remaining.desired_state == "cancel"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "job_type"),
    (
        ("/api/pipeline/backup-all", "poster_backup_all"),
        ("/api/pipeline/posters/reset", "poster_deploy_reset"),
        ("/api/system/heal", "poster_heal"),
    ),
)
async def test_mutation_parent_routes_require_idempotency_and_return_canonical_links(
    db, client: AsyncClient, path: str, job_type: str
) -> None:
    assert (await client.post(path)).status_code == 422
    response = await client.post(path, headers={"Idempotency-Key": f"{job_type}:route-test"})
    assert response.status_code == 202
    body = response.json()
    assert body["disposition"] == "created"
    assert body["phase"] == "terminal"
    assert body["snapshot_url"] == f"/api/jobs/{body['job_id']}/snapshot"
    assert body["detail_url"] == f"/projection-room/jobs/{body['job_id']}"
    job = await db.get(Job, body["job_id"])
    assert job is not None and job.type == job_type
    assert job.pgq_job_id is None and job.dispatch_generation == 0
