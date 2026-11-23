"""Text profile persistence, resolution, and OCR-gate knob overrides."""

import pytest
from httpx import ASGITransport, AsyncClient

import marquee.core.text_profiles as tp
from marquee.core.jobs.internal_runner import _ocr_gate_context
from marquee.core.text_profiles import (
    TextProfileError,
    create_profile,
    delete_profile,
    get_active_profile,
    get_default_profile_id,
    load_profiles,
    set_default_profile,
    update_profile,
)
from marquee.main import app
from marquee.models import Movie, Series
from marquee.pipeline.ocr_filter import (
    PosterTextFilter,
    _DetectedBox,
    _season_category_evidence,
    classify_text_box,
)


@pytest.fixture(autouse=True)
def isolated_profiles(tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "_PROFILES_PATH", tmp_path / "text_profiles.json")


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def test_season_profile_snapshot_survives_later_default_changes():
    from types import SimpleNamespace

    snapshot = load_profiles("season")["title_season_and_name"].to_dict()
    set_default_profile("season", "title_only")

    context = _ocr_gate_context(
        {
            "text_gate": {
                "scope": "season",
                "profile_id": "title_only",
                "profile_snapshot": snapshot,
            }
        },
        SimpleNamespace(media_type="season", season_number=5),
    )

    assert context.profile is not None
    assert context.profile.id == "title_season_and_name"
    assert context.profile.gate_payload()["allow_season"] is True
    assert context.profile.gate_payload()["allow_season_title"] is True


def test_present_malformed_profile_snapshot_fails_instead_of_reloading():
    from types import SimpleNamespace

    with pytest.raises(ValueError, match="malformed profile snapshot"):
        _ocr_gate_context(
            {
                "text_gate": {
                    "scope": "season",
                    "profile_id": "title_season_and_name",
                    "profile_snapshot": {"id": "title_season_and_name"},
                }
            },
            SimpleNamespace(media_type="season", season_number=5),
        )


# ── Season OCR Classification ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_api_crud_and_default(client: AsyncClient):
    listed = (await client.get("/api/text-profiles")).json()
    assert listed["scopes"]["movie"]["default_id"] == "title_only"
    assert {p["id"] for p in listed["scopes"]["movie"]["profiles"]} == {
        "title_only",
        "textless",
        "title_optional",
    }

    created = await client.post(
        "/api/text-profiles/movie",
        json={"name": "Clean Credits", "settings": {"mode": "custom", "allow_director": True}},
    )
    assert created.status_code == 201
    profile_id = created.json()["id"]
    assert profile_id == "clean_credits"

    updated = await client.put(
        f"/api/text-profiles/movie/{profile_id}",
        json={"settings": {"mode": "custom", "allow_studio": True}},
    )
    assert updated.status_code == 200
    assert updated.json()["settings"]["allow_studio"] is True

    assert (await client.put(f"/api/text-profiles/movie/default/{profile_id}")).status_code == 200
    assert (await client.get("/api/text-profiles")).json()["scopes"]["movie"][
        "default_id"
    ] == profile_id

    deleted = await client.delete(f"/api/text-profiles/movie/{profile_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": profile_id}
    assert (await client.get("/api/text-profiles")).json()["scopes"]["movie"][
        "default_id"
    ] == "title_only"


@pytest.mark.asyncio
async def test_api_builtin_protection_and_errors(client: AsyncClient):
    assert (await client.delete("/api/text-profiles/movie/title_only")).status_code == 400
    assert (
        await client.put("/api/text-profiles/movie/textless", json={"name": "No"})
    ).status_code == 400
    assert (await client.put("/api/text-profiles/movie/default/unknown")).status_code == 404
    assert (
        await client.post(
            "/api/text-profiles/movie", json={"name": "Bad", "settings": {"mode": "x"}}
        )
    ).status_code == 400


@pytest.mark.asyncio
async def test_api_movie_override_roundtrip(client: AsyncClient, db):
    movie = Movie(title="Dune", year=2021, folder_path="/movies/Dune")
    db.add(movie)
    await db.commit()

    current = await client.get(f"/api/text-profiles/movie/{movie.id}")
    assert current.status_code == 200
    assert current.json() == {
        "movie_id": movie.id,
        "profile_id": None,
        "effective_id": "title_only",
    }

    created = await client.post(
        "/api/text-profiles/movie",
        json={"name": "Credits OK", "settings": {"mode": "custom", "allow_director": True}},
    )
    profile_id = created.json()["id"]

    updated = await client.put(
        f"/api/text-profiles/movie/{movie.id}",
        json={"profile_id": profile_id},
    )
    assert updated.status_code == 200
    assert updated.json() == {
        "movie_id": movie.id,
        "profile_id": profile_id,
        "effective_id": profile_id,
    }

    await db.refresh(movie)
    assert movie.text_profile_id == profile_id

    reset = await client.put(f"/api/text-profiles/movie/{movie.id}", json={"profile_id": None})
    assert reset.status_code == 200
    assert reset.json()["effective_id"] == "title_only"

    await db.refresh(movie)
    assert movie.text_profile_id is None


@pytest.mark.asyncio
async def test_api_series_override_roundtrip(client: AsyncClient, db):
    series = Series(title="Breaking Bad", year=2008, series_path="/tv/Breaking Bad", sonarr_id=1)
    db.add(series)
    await db.commit()

    current = await client.get(f"/api/text-profiles/series/{series.id}")
    assert current.status_code == 200
    assert current.json()["show_profile_id"] is None
    assert current.json()["season_profile_id"] is None
    assert current.json()["effective_show"]["id"] == "title_only"
    assert current.json()["effective_season"]["id"] == "title_and_season"

    # Create custom profiles
    c_show = await client.post(
        "/api/text-profiles/show",
        json={"name": "Show Custom", "settings": {"mode": "custom", "allow_director": True}},
    )
    assert c_show.status_code == 201
    show_profile_id = c_show.json()["id"]

    c_season = await client.post(
        "/api/text-profiles/season",
        json={"name": "Season Custom", "settings": {"mode": "custom", "allow_season": True}},
    )
    assert c_season.status_code == 201
    season_profile_id = c_season.json()["id"]

    # Set override
    updated = await client.put(
        f"/api/text-profiles/series/{series.id}",
        json={"show_profile_id": show_profile_id, "season_profile_id": season_profile_id},
    )
    assert updated.status_code == 200
    assert updated.json()["show_profile_id"] == show_profile_id
    assert updated.json()["season_profile_id"] == season_profile_id
    assert updated.json()["effective_show"]["id"] == show_profile_id
    assert updated.json()["effective_season"]["id"] == season_profile_id

    # Reset
    reset = await client.put(
        f"/api/text-profiles/series/{series.id}",
        json={"show_profile_id": None, "season_profile_id": None},
    )
    assert reset.status_code == 200
    assert reset.json()["show_profile_id"] is None
    assert reset.json()["effective_show"]["id"] == "title_only"


@pytest.mark.asyncio
async def test_api_movie_override_validation(client: AsyncClient, db):
    movie = Movie(title="Heat", year=1995, folder_path="/movies/Heat")
    db.add(movie)
    await db.commit()

    missing_movie = await client.get("/api/text-profiles/movie/999999")
    assert missing_movie.status_code == 404

    bad_profile = await client.put(
        f"/api/text-profiles/movie/{movie.id}",
        json={"profile_id": "missing"},
    )
    assert bad_profile.status_code == 400


@pytest.mark.asyncio
async def test_config_api_no_longer_groups_text_gate(client: AsyncClient):
    config = (await client.get("/api/config/pipeline")).json()
    assert all(group["id"] != "text_gate" for group in config["groups"])
    # The raw knobs still exist for backward compat.
    assert "OCR_TEXT_MODE" in config["values"]
    assert "OCR_TEXT_MODE" in config["meta"]
