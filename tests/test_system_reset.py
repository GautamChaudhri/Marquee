"""Tests for POST /api/system/reset-db."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from marquee.database import _get_session_factory
from marquee.main import app
from marquee.models import Job, Movie


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_reset_db_endpoint_clears_application_tables(db, client: AsyncClient):
    movie = Movie(title="Reset Me", year=2024, folder_path="/movies/reset", tmdb_id=424242)
    job = Job(id="reset-job", type="maintenance", status="queued")
    db.add_all([movie, job])
    await db.commit()

    before_movies = await db.scalar(select(func.count()).select_from(Movie))
    before_jobs = await db.scalar(select(func.count()).select_from(Job))
    assert before_movies == 1
    assert before_jobs == 1

    await db.close()
    response = await client.post("/api/system/reset-db")

    assert response.status_code == 200
    assert response.json()["status"] == "reset"
    assert response.json()["tables_cleared"] > 0

    factory = _get_session_factory()
    async with factory() as verify_db:
        after_movies = await verify_db.scalar(select(func.count()).select_from(Movie))
        after_jobs = await verify_db.scalar(select(func.count()).select_from(Job))
        assert after_movies == 0
        assert after_jobs == 0
