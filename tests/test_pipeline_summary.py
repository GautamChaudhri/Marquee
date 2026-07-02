from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.main import app
from marquee.models import Job, JobSchedule, Movie, PipelineRun


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_pipeline_summary_counts_jobs_heal_and_backups(
    db: AsyncSession, client: AsyncClient, monkeypatch, tmp_path
):
    monkeypatch.setattr(
        type(settings),
        "poster_backup_path",
        property(lambda self: tmp_path / "backups" / "posters"),
    )
    settings.poster_backup_path.mkdir(parents=True)
    (settings.poster_backup_path / "1.jpg").write_bytes(b"poster")

    now = datetime.now(UTC)
    with_poster = Movie(
        title="Alpha",
        year=2020,
        folder_path=str(tmp_path / "a"),
        movie_file_path="alpha.mkv",
        tmdb_id=1,
        poster_path=str(tmp_path / "a" / "poster.jpg"),
    )
    missing = Movie(
        title="Bravo",
        year=2021,
        folder_path=str(tmp_path / "b"),
        movie_file_path="bravo.mkv",
        tmdb_id=2,
    )
    undownloaded = Movie(title="Charlie", year=2022, folder_path=str(tmp_path / "c"), tmdb_id=3)
    db.add_all([with_poster, missing, undownloaded])
    await db.flush()
    db.add_all(
        [
            PipelineRun(
                run_id="review-alpha",
                movie_id=with_poster.id,
                status="completed",
                started_at=now - timedelta(minutes=10),
            ),
            PipelineRun(
                run_id="running-bravo",
                movie_id=missing.id,
                status="running",
                started_at=now,
                batch_id="batch-1",
            ),
            Job(
                id="batch-1",
                type="poster_pipeline_batch",
                payload={"movie_ids": [with_poster.id, missing.id]},
                status="running",
                subject_type="pipeline_batch",
                subject_id="missing",
            ),
            Job(
                id="heal-1",
                type="poster_heal",
                payload={},
                status="succeeded",
                finished_at=now,
                result={"checked": 2, "restored": 1, "failed": 0},
            ),
            JobSchedule(
                id="poster-heal",
                job_type="poster_heal",
                interval_seconds=900,
                enabled=True,
                next_run_at=now + timedelta(minutes=15),
            ),
        ]
    )
    await db.commit()

    response = await client.get("/api/pipeline/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["total_movies"] == 2
    assert body["movies_with_poster"] == 1
    assert body["movies_missing_poster"] == 1
    assert body["movies_in_review"] == 1
    assert body["movies_in_run"] == 1
    assert body["running_jobs"][0]["job_id"] == "batch-1"
    assert body["running_jobs"][0]["movie_count"] == 2
    assert body["last_heal"]["checked"] == 2
    assert body["heal_schedule"]["interval_minutes"] == 15
    assert body["backups"] == {"count": 1, "bytes": 6}
