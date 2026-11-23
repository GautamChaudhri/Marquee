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
    assert set(profiles) == {"title_only", "textless", "title_optional"}
    assert profiles["title_only"].builtin
    assert profiles["title_only"].is_default
    assert profiles["textless"].settings.mode == "textless"

    profiles_season = load_profiles("season")
    assert "title_and_season" in profiles_season
    assert profiles_season["title_and_season"].builtin
    assert profiles_season["title_and_season"].is_default
    assert profiles_season["title_season_and_name"].settings.allow_season_title
    assert profiles_season["all_season_text"].settings.allow_season_edition


def test_create_update_delete_roundtrip():
    created = create_profile(
        "movie", "Director + Title", {"mode": "custom", "allow_director": True}
    )
    assert created.id == "director_title"
    assert created.settings.allow_director is True

    # Persists across a fresh load.
    profiles = load_profiles("movie")
    assert "director_title" in profiles
    assert profiles["director_title"].settings.mode == "custom"

    updated = update_profile(
        "movie", "director_title", settings={"mode": "custom", "allow_studio": True}
    )
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
    assert filt.task_extras() == {
        "profile": filt.profile_payload,
        "profile_id": "strict",
        "profile_name": "Strict",
        "profile_scope": "movie",
    }


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


def test_season_ocr_classification():
    # Verify season designator patterns classify as "season"
    box_s3 = _box("SEASON 3")
    assert (
        classify_text_box(
            box_s3,
            image_w=500,
            image_h=750,
            title_box=None,
            title_tokens=set(),
            director_tokens=set(),
        )
        == "season"
    )


@pytest.mark.parametrize("text", ["SEASON FIVE", "SEASONFIVE", "FIVE SEASON", "BOOK V"])
def test_expected_written_season_number_classifies_as_season(text: str):
    assert (
        classify_text_box(
            _box(text),
            image_w=500,
            image_h=750,
            title_box=None,
            title_tokens=set(),
            director_tokens=set(),
            season_number=5,
        )
        == "season"
    )


def test_split_expected_season_phrase_marks_both_fragments_as_season():
    designator = _box("SEASON", left=10, right=110)
    number = _box("FIVE", left=120, right=210)
    number_evidence, _title_evidence, _edition_evidence = _season_category_evidence(
        [designator, number],
        image_height=750,
        season_number=5,
        season_title=None,
    )
    for box in (designator, number):
        assert (
            classify_text_box(
                box,
                image_w=500,
                image_h=750,
                title_box=None,
                title_tokens=set(),
                director_tokens=set(),
                season_number=5,
                season_number_ids=number_evidence,
            )
            == "season"
        )


def test_stacked_expected_season_phrase_marks_both_fragments_as_season():
    designator = _box("SEASON", left=80, right=220, top=500, bottom=540)
    number = _box("FIVE", left=105, right=195, top=555, bottom=595)
    number_evidence, _title_evidence, _edition_evidence = _season_category_evidence(
        [designator, number],
        image_width=500,
        image_height=750,
        season_number=5,
        season_title=None,
    )
    assert {("season", designator.bbox), ("five", number.bbox)} <= number_evidence


@pytest.mark.parametrize(
    ("number", "text"),
    [(21, "SEASON TWENTY ONE"), (32, "THIRTY SECOND SEASON"), (25, "BOOK XXV")],
)
def test_written_and_roman_season_numbers_above_twenty(number: int, text: str):
    assert (
        classify_text_box(
            _box(text),
            image_w=500,
            image_h=750,
            title_box=None,
            title_tokens=set(),
            director_tokens=set(),
            season_number=number,
        )
        == "season"
    )


@pytest.mark.parametrize("text", ["SEASON", "FIVE", "V"])
def test_isolated_expected_season_fragment_is_not_sufficient(text: str):
    assert (
        classify_text_box(
            _box(text),
            image_w=500,
            image_h=750,
            title_box=None,
            title_tokens=set(),
            director_tokens=set(),
            season_number=5,
        )
        != "season"
    )


def test_distant_expected_number_is_not_absorbed_by_another_season_phrase():
    phrase = _box("SEASON FIVE", left=20, right=220, top=80, bottom=120)
    distant = _box("FIVE", left=350, right=430, top=680, bottom=720)
    number_evidence, _title_evidence, _edition_evidence = _season_category_evidence(
        [phrase, distant],
        image_width=500,
        image_height=750,
        season_number=5,
        season_title=None,
    )
    assert ("season five", phrase.bbox) in number_evidence
    assert ("five", distant.bbox) not in number_evidence


def test_wrong_written_season_number_is_not_allowed_as_season_text():
    assert (
        classify_text_box(
            _box("SEASON SIX"),
            image_w=500,
            image_h=750,
            title_box=None,
            title_tokens=set(),
            director_tokens=set(),
            season_number=5,
        )
        != "season"
    )


def test_official_season_name_and_complete_edition_evidence():
    named = _box("BOOK ONE: WATER", top=100, bottom=140)
    edition = _box("THE COMPLETE SECOND SEASON", top=200, bottom=240)
    number_evidence, title_evidence, edition_evidence = _season_category_evidence(
        [named, edition],
        image_height=750,
        season_number=2,
        season_title="Book One: Water",
    )
    assert (
        classify_text_box(
            named,
            image_w=500,
            image_h=750,
            title_box=None,
            title_tokens=set(),
            director_tokens=set(),
            season_number=2,
            season_number_ids=number_evidence,
            season_title_ids=title_evidence,
            season_edition_ids=edition_evidence,
        )
        == "season_title"
    )
    assert (
        classify_text_box(
            edition,
            image_w=500,
            image_h=750,
            title_box=None,
            title_tokens=set(),
            director_tokens=set(),
            season_number=2,
            season_number_ids=number_evidence,
            season_title_ids=title_evidence,
            season_edition_ids=edition_evidence,
        )
        == "season_edition"
    )


def test_split_name_and_edition_preserve_profile_category_boundaries():
    book = _box("BOOK ONE", left=10, right=120, top=100, bottom=140)
    water = _box("WATER", left=130, right=220, top=100, bottom=140)
    complete = _box("COMPLETE", left=10, right=110, top=220, bottom=260)
    second_season = _box("SECOND SEASON", left=120, right=280, top=220, bottom=260)
    number_evidence, title_evidence, edition_evidence = _season_category_evidence(
        [book, water, complete, second_season],
        image_width=500,
        image_height=750,
        season_number=1,
        season_title="Book One: Water",
    )
    assert ("book one", book.bbox) in number_evidence
    assert ("book one", book.bbox) not in title_evidence
    assert ("water", water.bbox) in title_evidence

    # Re-evaluate the packaging pair for season two: both pieces are governed by
    # the edition toggle, even though SECOND SEASON is valid numbering by itself.
    number_evidence, _title_evidence, edition_evidence = _season_category_evidence(
        [complete, second_season],
        image_width=500,
        image_height=750,
        season_number=2,
        season_title=None,
    )
    assert not number_evidence
    assert {("complete", complete.bbox), ("second season", second_season.bbox)} <= edition_evidence


def test_generic_official_season_name_stays_under_season_toggle():
    generic = _box("SEASON FIVE")
    number_evidence, title_evidence, _edition_evidence = _season_category_evidence(
        [generic],
        image_width=500,
        image_height=750,
        season_number=5,
        season_title="Season Five",
    )
    assert ("season five", generic.bbox) in number_evidence
    assert not title_evidence


def test_legacy_season_patterns_without_expected_number():

    box_s01 = _box("s01")
    assert (
        classify_text_box(
            box_s01,
            image_w=500,
            image_h=750,
            title_box=None,
            title_tokens=set(),
            director_tokens=set(),
        )
        == "season"
    )

    box_num = _box("3")
    assert (
        classify_text_box(
            box_num,
            image_w=500,
            image_h=750,
            title_box=None,
            title_tokens=set(),
            director_tokens=set(),
        )
        == "season"
    )


# ── API Routes ────────────────────────────────────────────────────────────


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
