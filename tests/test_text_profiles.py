"""Text profile persistence, resolution, and OCR-gate knob overrides."""

import pytest
from httpx import ASGITransport, AsyncClient

import marquee.core.text_profiles as tp
from marquee.core.pipeline_config import pipeline_settings
from marquee.core.text_profiles import (
    OcrGateContext,
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
from marquee.models import Movie
from marquee.pipeline.ocr_filter import (
    PosterTextFilter,
    _detect_top_billing_bands,
    _DetectedBox,
    classify_text_box,
    effective_gate_knobs,
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
    profiles = load_profiles()
    assert set(profiles) == {"title_only", "textless"}
    assert profiles["title_only"].builtin
    assert profiles["title_only"].is_default
    assert profiles["textless"].settings.mode == "textless"


def test_create_update_delete_roundtrip():
    created = create_profile("Director + Title", {"mode": "custom", "allow_director": True})
    assert created.id == "director_title"
    assert created.settings.allow_director is True

    # Persists across a fresh load.
    profiles = load_profiles()
    assert "director_title" in profiles
    assert profiles["director_title"].settings.mode == "custom"

    updated = update_profile("director_title", settings={"mode": "custom", "allow_studio": True})
    assert updated.settings.allow_studio is True
    assert load_profiles()["director_title"].settings.allow_studio is True

    delete_profile("director_title")
    assert "director_title" not in load_profiles()


def test_name_validation():
    create_profile("My Profile", {})
    with pytest.raises(TextProfileError):
        create_profile("my profile", {})  # duplicate (case-insensitive)
    with pytest.raises(TextProfileError):
        create_profile("", {})
    with pytest.raises(TextProfileError):
        create_profile("x" * 65, {})
    with pytest.raises(TextProfileError):
        create_profile("Title Only", {})  # collides with built-in name


def test_settings_validation():
    with pytest.raises(TextProfileError):
        create_profile("Bad Mode", {"mode": "nope"})
    with pytest.raises(TextProfileError):
        create_profile("Bad Boxes", {"max_residual_boxes": 21})
    with pytest.raises(TextProfileError):
        create_profile("Bad Area", {"max_residual_area_fraction": 1.5})


def test_builtins_are_immutable():
    with pytest.raises(TextProfileError):
        update_profile("title_only", name="Renamed")
    with pytest.raises(TextProfileError):
        delete_profile("textless")


def test_default_selection_and_fallback_on_delete():
    create_profile("Loose", {"mode": "custom", "max_residual_boxes": 3})
    set_default_profile("loose")
    assert get_default_profile_id() == "loose"
    assert get_active_profile().id == "loose"

    delete_profile("loose")
    assert get_default_profile_id() == "title_only"


def test_active_profile_resolution_order():
    create_profile("Override Me", {"mode": "textless"})
    # Unknown override falls back to the default.
    assert get_active_profile("missing").id == "title_only"
    # Known override wins over default.
    assert get_active_profile("override_me").id == "override_me"
    with pytest.raises(TextProfileError):
        set_default_profile("missing")


# ── Gate payload semantics ────────────────────────────────────────────────


def test_gate_payload_builtin_pins_mode_only():
    profiles = load_profiles()
    assert profiles["title_only"].gate_payload() == {"mode": "title_only"}
    assert profiles["textless"].gate_payload() == {"mode": "textless"}


def test_gate_payload_custom_is_explicit():
    created = create_profile("Full", {"mode": "custom", "allow_rating": True})
    payload = created.gate_payload()
    assert payload["mode"] == "custom"
    assert payload["allow_rating"] is True
    assert payload["max_residual_boxes"] == 0
    assert payload["require_title"] is True


def test_effective_gate_knobs_fallback_and_override():
    # No profile → raw pipeline_settings.
    knobs = effective_gate_knobs(None)
    assert knobs["mode"] == pipeline_settings.OCR_TEXT_MODE
    assert knobs["allow_map"]["title"] == pipeline_settings.OCR_ALLOW_TITLE
    assert knobs["allow_map"]["billing"] is False
    assert knobs["require_title"] == pipeline_settings.OCR_REQUIRE_TITLE

    # Partial profile → profile keys win, the rest falls back.
    knobs = effective_gate_knobs({"mode": "textless"})
    assert knobs["mode"] == "textless"
    assert knobs["max_residual_boxes"] == pipeline_settings.OCR_MAX_RESIDUAL_BOXES

    # Full custom profile.
    knobs = effective_gate_knobs(
        {
            "mode": "custom",
            "allow_title": True,
            "allow_director": True,
            "allow_billing": True,
            "max_residual_boxes": 5,
            "max_residual_area_fraction": 0.1,
            "require_title": False,
        }
    )
    assert knobs["allow_map"]["director"] is True
    assert knobs["allow_map"]["billing"] is True
    assert knobs["max_residual_boxes"] == 5
    assert knobs["require_title"] is False


def test_poster_text_filter_carries_profile_payload():
    profile = create_profile("Strict", {"mode": "custom", "allow_tagline": True})
    filt = PosterTextFilter("Dune", profile=profile)
    assert filt.profile_payload["allow_tagline"] is True
    assert filt.task_extras() == {"profile": filt.profile_payload}
    # No profile → no extras → workers fall back to pipeline_settings.
    assert PosterTextFilter("Dune").task_extras() is None


def test_classify_text_box_matches_director_anchor():
    box = _box("A film by Nolan")
    assert (
        classify_text_box(
            box,
            image_w=1000,
            image_h=1500,
            title_box=None,
            title_tokens={"dune"},
            director_tokens={"christopher", "nolan"},
        )
        == "director"
    )


def test_classify_text_box_matches_studio_tokens():
    box = _box("Syncopy Films")
    assert (
        classify_text_box(
            box,
            image_w=1000,
            image_h=1500,
            title_box=None,
            title_tokens={"dune"},
            director_tokens=set(),
            studio_tokens={"syncopy", "films"},
        )
        == "studio"
    )


def test_classify_text_box_matches_tmdb_tagline():
    box = _box("Long live the fighters", left=10, top=1280, right=420, bottom=1340)
    assert (
        classify_text_box(
            box,
            image_w=1000,
            image_h=1500,
            title_box=None,
            title_tokens={"dune"},
            director_tokens=set(),
            tagline_text="Long live the fighter!",
        )
        == "tagline"
    )


def test_classify_text_box_matches_rating():
    box = _box("Rated PG-13")
    assert (
        classify_text_box(
            box,
            image_w=1000,
            image_h=1500,
            title_box=None,
            title_tokens={"dune"},
            director_tokens=set(),
        )
        == "rating"
    )


def test_classify_text_box_prefers_billing_band_ids():
    box = _box("Christopher Nolan", left=70, top=20, right=260, bottom=52, confidence=0.8)
    assert (
        classify_text_box(
            box,
            image_w=1000,
            image_h=1500,
            title_box=None,
            title_tokens={"dune"},
            director_tokens={"christopher", "nolan"},
            billing_band_ids={id(box)},
        )
        == "billing"
    )


def test_detect_top_billing_bands_flags_actor_strip():
    boxes = [
        _box("Timothee", left=70, top=20, right=210, bottom=52, confidence=0.8),
        _box("Zendaya", left=280, top=22, right=420, bottom=54, confidence=0.82),
        _box("Rebecca", left=500, top=24, right=640, bottom=56, confidence=0.81),
    ]
    ids = _detect_top_billing_bands(boxes, image_width=1000, image_height=1500)
    assert ids == {id(box) for box in boxes}


def test_detect_top_billing_bands_ignores_format_junk_members():
    actor_boxes = [
        _box("Timothee", left=70, top=20, right=210, bottom=52, confidence=0.8),
        _box("Zendaya", left=280, top=22, right=420, bottom=54, confidence=0.82),
        _box("Rebecca", left=500, top=24, right=640, bottom=56, confidence=0.81),
    ]
    junk = _box("IMAX", left=760, top=20, right=860, bottom=52, confidence=0.85)
    ids = _detect_top_billing_bands([*actor_boxes, junk], image_width=1000, image_height=1500)
    assert ids == {id(box) for box in actor_boxes}


def test_detect_top_billing_bands_rejects_narrow_strip():
    boxes = [
        _box("Timothee", left=70, top=20, right=130, bottom=52, confidence=0.8),
        _box("Zendaya", left=150, top=22, right=210, bottom=54, confidence=0.82),
        _box("Rebecca", left=230, top=24, right=290, bottom=56, confidence=0.81),
    ]
    assert _detect_top_billing_bands(boxes, image_width=1000, image_height=1500) == set()


def test_detect_top_billing_bands_rejects_inconsistent_heights():
    boxes = [
        _box("Timothee", left=70, top=20, right=210, bottom=52, confidence=0.8),
        _box("Zendaya", left=280, top=22, right=420, bottom=54, confidence=0.82),
        _box("Rebecca", left=500, top=10, right=640, bottom=120, confidence=0.81),
    ]
    assert _detect_top_billing_bands(boxes, image_width=1000, image_height=1500) == set()


def test_ocr_gate_context_from_movie_without_columns():
    class Stub:
        pass

    ctx = OcrGateContext.from_movie(Stub())
    assert ctx.director is None
    assert ctx.profile.id == "title_only"


# ── API endpoints ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_api_crud_and_default(client: AsyncClient):
    listed = (await client.get("/api/text-profiles")).json()
    assert listed["default_id"] == "title_only"
    assert {p["id"] for p in listed["profiles"]} == {"title_only", "textless"}

    created = await client.post(
        "/api/text-profiles",
        json={"name": "Clean Credits", "settings": {"mode": "custom", "allow_director": True}},
    )
    assert created.status_code == 201
    profile_id = created.json()["id"]
    assert profile_id == "clean_credits"

    updated = await client.put(
        f"/api/text-profiles/{profile_id}",
        json={"settings": {"mode": "custom", "allow_studio": True}},
    )
    assert updated.status_code == 200
    assert updated.json()["settings"]["allow_studio"] is True

    assert (await client.put(f"/api/text-profiles/default/{profile_id}")).status_code == 200
    assert (await client.get("/api/text-profiles")).json()["default_id"] == profile_id

    deleted = await client.delete(f"/api/text-profiles/{profile_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": profile_id}
    assert (await client.get("/api/text-profiles")).json()["default_id"] == "title_only"


@pytest.mark.asyncio
async def test_api_builtin_protection_and_errors(client: AsyncClient):
    assert (await client.delete("/api/text-profiles/title_only")).status_code == 400
    assert (await client.put("/api/text-profiles/textless", json={"name": "No"})).status_code == 400
    assert (await client.put("/api/text-profiles/default/unknown")).status_code == 404
    assert (
        await client.post("/api/text-profiles", json={"name": "Bad", "settings": {"mode": "x"}})
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
        "/api/text-profiles",
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
