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
from marquee.pipeline.ocr_filter import PosterTextFilter, effective_gate_knobs


@pytest.fixture(autouse=True)
def isolated_profiles(tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "_PROFILES_PATH", tmp_path / "text_profiles.json")


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


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
    assert (
        await client.put("/api/text-profiles/textless", json={"name": "No"})
    ).status_code == 400
    assert (await client.put("/api/text-profiles/default/unknown")).status_code == 404
    assert (
        await client.post("/api/text-profiles", json={"name": "Bad", "settings": {"mode": "x"}})
    ).status_code == 400


@pytest.mark.asyncio
async def test_config_api_no_longer_groups_text_gate(client: AsyncClient):
    config = (await client.get("/api/config/pipeline")).json()
    assert all(group["id"] != "text_gate" for group in config["groups"])
    # The raw knobs still exist for backward compat.
    assert "OCR_TEXT_MODE" in config["values"]
    assert "OCR_TEXT_MODE" in config["meta"]
