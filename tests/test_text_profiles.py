"""Text profile persistence, resolution, and OCR-gate knob overrides."""

import pytest
from httpx import ASGITransport, AsyncClient

import marquee.core.text_profiles as tp
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


def _box(
    text: str,
    *,
    left: float = 10,
    top: float = 10,
    right: float = 210,
    bottom: float = 60,
    confidence: float = 0.99,
) -> _DetectedBox:
    return _DetectedBox(
        text=text,
        confidence=confidence,
        bbox=((left, top), (right, top), (right, bottom), (left, bottom)),
    )


# ── Persistence + CRUD ────────────────────────────────────────────────────


def test_builtins_always_present_without_file():
    profiles = load_profiles("movie")
    assert set(profiles) == {"title_only", "textless"}
    assert profiles["title_only"].builtin
    assert profiles["title_only"].is_default
    assert profiles["textless"].settings.mode == "textless"

    profiles_season = load_profiles("season")
    assert "title_and_season" in profiles_season
    assert profiles_season["title_and_season"].builtin
    assert profiles_season["title_and_season"].is_default


def test_create_update_delete_roundtrip():
    created = create_profile("movie", "Director + Title", {"mode": "custom", "allow_director": True})
    assert created.id == "director_title"
    assert created.settings.allow_director is True

    # Persists across a fresh load.
    profiles = load_profiles("movie")
    assert "director_title" in profiles
    assert profiles["director_title"].settings.mode == "custom"

    updated = update_profile("movie", "director_title", settings={"mode": "custom", "allow_studio": True})
    assert updated.settings.allow_studio is True
    assert load_profiles("movie")["director_title"].settings.allow_studio is True

    delete_profile("movie", "director_title")
    assert "director_title" not in load_profiles("movie")


def test_name_validation():
    create_profile("movie", "My Profile", {})
    with pytest.raises(TextProfileError):
        create_profile("movie", "my profile", {})  # duplicate (case-insensitive)
    with pytest.raises(TextProfileError):
        create_profile("movie", "", {})
    with pytest.raises(TextProfileError):
        create_profile("movie", "x" * 65, {})
    with pytest.raises(TextProfileError):
        create_profile("movie", "Title Only", {})  # collides with built-in name


def test_settings_validation():
    with pytest.raises(TextProfileError):
        create_profile("movie", "Bad Mode", {"mode": "nope"})
    with pytest.raises(TextProfileError):
        create_profile("movie", "Bad Boxes", {"max_residual_boxes": 21})
    with pytest.raises(TextProfileError):
        create_profile("movie", "Bad Area", {"max_residual_area_fraction": 1.5})


def test_builtins_are_immutable():
    with pytest.raises(TextProfileError):
        update_profile("movie", "title_only", name="Renamed")
    with pytest.raises(TextProfileError):
        delete_profile("movie", "textless")


def test_default_selection_and_fallback_on_delete():
    create_profile("movie", "Loose", {"mode": "custom", "max_residual_boxes": 3})
    set_default_profile("movie", "loose")
    assert get_default_profile_id("movie") == "loose"
    assert get_active_profile("movie").id == "loose"

    delete_profile("movie", "loose")
    assert get_default_profile_id("movie") == "title_only"


def test_active_profile_resolution_order():
    create_profile("movie", "Override Me", {"mode": "textless"})
    # Unknown override falls back to the default.
    assert get_active_profile("movie", "missing").id == "title_only"
    # Known override wins over default.
    assert get_active_profile("movie", "override_me").id == "override_me"
    with pytest.raises(TextProfileError):
        set_default_profile("movie", "missing")


# ── Gate payload semantics ────────────────────────────────────────────────


def test_gate_payload_builtin_pins_mode_only():
    profiles = load_profiles("movie")
    assert profiles["title_only"].gate_payload() == {"mode": "title_only"}


def test_gate_payload_custom_is_explicit():
    profile = create_profile("movie", "Strict", {"mode": "custom", "allow_tagline": True})
    payload = profile.gate_payload()
    assert payload["mode"] == "custom"
    assert payload["allow_tagline"] is True
    assert payload["allow_director"] is False  # code defaults ignored, profile is source of truth


def test_poster_text_filter_carries_profile_payload():
    profile = create_profile("movie", "Strict", {"mode": "custom", "allow_tagline": True})
    filt = PosterTextFilter("Dune", profile=profile)
    assert filt.profile_payload["allow_tagline"] is True
    assert filt.task_extras() == {"profile": filt.profile_payload}


# ── Season OCR Classification ─────────────────────────────────────────────

def test_season_ocr_classification():
    # Verify season designator patterns classify as "season"
    box_s3 = _box("SEASON 3")
    assert classify_text_box(
        box_s3,
        image_w=500,
        image_h=750,
        title_box=None,
        title_tokens=set(),
        director_tokens=set(),
    ) == "season"

    box_s01 = _box("s01")
    assert classify_text_box(
        box_s01,
        image_w=500,
        image_h=750,
        title_box=None,
        title_tokens=set(),
        director_tokens=set(),
    ) == "season"

    box_num = _box("3")
    assert classify_text_box(
        box_num,
        image_w=500,
        image_h=750,
        title_box=None,
        title_tokens=set(),
        director_tokens=set(),
    ) == "season"


# ── API Routes ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_api_crud_and_default(client: AsyncClient):
    listed = (await client.get("/api/text-profiles")).json()
    assert listed["scopes"]["movie"]["default_id"] == "title_only"
    assert {p["id"] for p in listed["scopes"]["movie"]["profiles"]} == {"title_only", "textless"}

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
    assert (await client.get("/api/text-profiles")).json()["scopes"]["movie"]["default_id"] == profile_id

    deleted = await client.delete(f"/api/text-profiles/movie/{profile_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": profile_id}
    assert (await client.get("/api/text-profiles")).json()["scopes"]["movie"]["default_id"] == "title_only"


@pytest.mark.asyncio
async def test_api_builtin_protection_and_errors(client: AsyncClient):
    assert (await client.delete("/api/text-profiles/movie/title_only")).status_code == 400
    assert (await client.put("/api/text-profiles/movie/textless", json={"name": "No"})).status_code == 400
    assert (await client.put("/api/text-profiles/movie/default/unknown")).status_code == 404
    assert (
        await client.post("/api/text-profiles/movie", json={"name": "Bad", "settings": {"mode": "x"}})
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
