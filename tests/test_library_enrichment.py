"""Tests for the enriched /api/library/movies list + filters (frontend G2)."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.main import app
from marquee.models import LetterboxState, MediaFile, Movie, SubtitleInventory


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _seed(db: AsyncSession) -> None:
    # Alpha — approved poster, 4K, Dolby Vision, LB candidate, subtitle gap.
    alpha = Movie(
        title="Alpha",
        year=2020,
        folder_path="/m/a",
        poster_path="/m/a/poster.jpg",
        poster_user_approved=True,
        video_width=3840,
        video_height=1600,
        has_hdr=True,
        has_dv=True,
        genres=["Sci-Fi"],
    )
    # Bravo — unreviewed AI pick, 1080p, HDR10, no LB row, no inventory.
    bravo = Movie(
        title="Bravo",
        year=2022,
        folder_path="/m/b",
        poster_path="/m/b/poster.jpg",
        poster_ai_selected=True,
        video_width=1920,
        video_height=1080,
        has_hdr=True,
        has_dv=False,
    )
    # Charlie — missing poster, no resolution, SDR, LB tagged, subtitle ok.
    charlie = Movie(
        title="Charlie",
        year=2019,
        folder_path="/m/c",
        has_hdr=False,
        has_dv=False,
    )
    db.add_all([alpha, bravo, charlie])
    await db.flush()

    db.add_all(
        [
            LetterboxState(movie_id=alpha.id, status="candidate"),
            LetterboxState(movie_id=charlie.id, status="tagged"),
        ]
    )
    mf_a = MediaFile(
        source="radarr", source_key="r:a", path="/m/a/a.mkv", movie_id=alpha.id, is_active=True
    )
    mf_c = MediaFile(
        source="radarr", source_key="r:c", path="/m/c/c.mkv", movie_id=charlie.id, is_active=True
    )
    db.add_all([mf_a, mf_c])
    await db.flush()

    db.add_all(
        [
            SubtitleInventory(
                media_file_id=mf_a.id,
                coverage_json=json.dumps({"missing_preferred_languages": ["eng"]}),
            ),
            SubtitleInventory(
                media_file_id=mf_c.id, coverage_json=json.dumps({"missing_preferred_languages": []})
            ),
        ]
    )
    await db.commit()


@pytest.mark.asyncio
async def test_list_enrichment_fields(db: AsyncSession, client: AsyncClient):
    await _seed(db)
    resp = await client.get("/api/library/movies?sort=title")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    by_title = {m["title"]: m for m in body["items"]}

    alpha = by_title["Alpha"]
    assert alpha["resolution"] == "4K"
    assert alpha["poster_status"] == "approved"
    assert alpha["hdr"] == "dovi"
    assert alpha["letterbox_status"] == "candidate"
    assert alpha["subtitle_status"] == "gap"
    assert alpha["genres"] == ["Sci-Fi"]

    assert "Bravo" not in by_title

    charlie = by_title["Charlie"]
    assert charlie["resolution"] is None
    assert charlie["poster_status"] == "missing"
    assert charlie["hdr"] == "sdr"
    assert charlie["letterbox_status"] == "tagged"
    assert charlie["subtitle_status"] == "ok"


@pytest.mark.asyncio
async def test_list_filters_and_sort(db: AsyncSession, client: AsyncClient):
    await _seed(db)

    r = await client.get("/api/library/movies?poster_status=missing")
    body = r.json()
    assert body["total"] == 1
    assert [m["title"] for m in body["items"]] == ["Charlie"]

    r = await client.get("/api/library/movies?hdr=dovi")
    assert [m["title"] for m in r.json()["items"]] == ["Alpha"]

    r = await client.get("/api/library/movies?letterbox_status=none")
    assert r.json()["items"] == []

    r = await client.get("/api/library/movies?q=bra")
    assert r.json()["total"] == 0

    r = await client.get("/api/library/movies?sort=year")
    assert [m["title"] for m in r.json()["items"]] == ["Alpha", "Charlie"]


@pytest.mark.asyncio
async def test_include_unavailable_returns_radarr_movies_without_files(
    db: AsyncSession, client: AsyncClient
):
    await _seed(db)

    r = await client.get("/api/library/movies?include_unavailable=true&q=bra")
    body = r.json()
    assert body["total"] == 1
    bravo = body["items"][0]
    assert bravo["title"] == "Bravo"
    assert bravo["media_file_id"] is None
    assert bravo["letterbox_status"] == "none"


@pytest.mark.asyncio
async def test_detail_has_derived_fields(db: AsyncSession, client: AsyncClient):
    await _seed(db)
    list_body = (await client.get("/api/library/movies?q=Alpha")).json()
    movie_id = list_body["items"][0]["id"]

    detail = (await client.get(f"/api/library/movies/{movie_id}")).json()
    assert detail["poster_status"] == "approved"
    assert detail["hdr"] == "dovi"
    assert detail["resolution"] == "4K"
    assert detail["letterbox_status"] == "candidate"
    assert "media_file_path" in detail
