"""Media-job confirm route tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from marquee.api.routes import media_jobs
from marquee.config import settings
from marquee.models import MediaFile, MediaJob, Movie


async def _make_job(db, tmp_path, monkeypatch, *, plan_expires_at):
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(tmp_path)])
    movie_dir = tmp_path / "Movie"
    movie_dir.mkdir()
    media_path = movie_dir / "Movie.mkv"
    media_path.write_bytes(b"data")

    movie = Movie(title="Movie", year=2024, folder_path=str(movie_dir), movie_file_path="Movie.mkv", tmdb_id=1)
    db.add(movie)
    await db.flush()
    media_file = MediaFile(
        source="radarr",
        source_key="radarr:movie:1",
        movie_id=movie.id,
        path=str(media_path),
        relative_path="Movie.mkv",
        container="mkv",
        is_active=True,
    )
    db.add(media_file)
    await db.flush()
    job = MediaJob(
        job_id="job1",
        operation="letterbox_reencode",
        media_file_id=media_file.id,
        status="planned",
        plan_expires_at=plan_expires_at,
    )
    db.add(job)
    await db.commit()
    return job


@pytest.mark.asyncio
async def test_confirm_job_with_naive_future_expiry_succeeds(db, tmp_path, monkeypatch):
    """Naive future expiry values should still be treated as valid."""
    naive_future = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)
    assert naive_future.tzinfo is None
    job = await _make_job(db, tmp_path, monkeypatch, plan_expires_at=naive_future)

    result = await media_jobs.confirm_job(job.job_id, db)

    assert result == {"job_id": "job1", "status": "queued"}


@pytest.mark.asyncio
async def test_confirm_job_with_naive_past_expiry_raises_plan_stale(db, tmp_path, monkeypatch):
    naive_past = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    assert naive_past.tzinfo is None
    job = await _make_job(db, tmp_path, monkeypatch, plan_expires_at=naive_past)

    with pytest.raises(HTTPException) as exc:
        await media_jobs.confirm_job(job.job_id, db)

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "plan_stale"
