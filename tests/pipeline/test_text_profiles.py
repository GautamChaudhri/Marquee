"""Text profile persistence, resolution, and OCR-gate knob overrides."""

import pytest

import marquee.pipeline.text_profiles as tp
from marquee.pipeline.ocr_filter import (
    PosterTextFilter,
    _DetectedBox,
    _season_category_evidence,
    classify_text_box,
)
from marquee.pipeline.text_profiles import (
    TextProfileError,
    create_profile,
    delete_profile,
    get_active_profile,
    get_default_profile_id,
    load_profiles,
    set_default_profile,
    update_profile,
)


@pytest.fixture(autouse=True)
def isolated_profiles(tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "_PROFILES_PATH", tmp_path / "text_profiles.json")


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
