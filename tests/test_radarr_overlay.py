"""Unit tests for Radarr overlay HDR / target helpers."""

from marquee.core.radarr_overlay import (
    classify_custom_format_tags,
    classify_hdr_tags,
    default_profile_preference,
    is_valid_preference_pair,
    ordered_tags,
    preference_status,
    preference_target_choices,
    profile_hdr_targets,
)


def test_classify_hdr_tags_variants():
    assert classify_hdr_tags("DV HDR10") == ["dovi", "hdr10"]
    assert classify_hdr_tags("DV") == ["dovi", "dovi_no_fallback"]
    assert classify_hdr_tags("HDR10Plus") == ["hdr10p"]
    assert classify_hdr_tags("HDR10") == ["hdr10"]
    assert classify_hdr_tags("HLG") == ["hdr"]
    assert classify_hdr_tags("PQ") == ["hdr"]
    assert classify_hdr_tags("SDR") == []
    assert classify_hdr_tags(None) == []


def test_classify_custom_format_tags_name_and_specs():
    assert classify_custom_format_tags("DV HDR10+", []) == {"dovi", "hdr10p"}
    assert classify_custom_format_tags(
        "Fancy Renamed Format",
        [
            {
                "fields": [
                    {"name": "value", "value": r"\b(HDR10PLUS|HDR10P|HDR10(\+|\b))"},
                    {"name": "other", "value": "ignored"},
                ]
            }
        ],
    ) == {"hdr10p"}


def test_profile_hdr_targets_only_positive_scores():
    class Item:
        def __init__(self, custom_format_id: int, score: int):
            self.custom_format_id = custom_format_id
            self.score = score

    targets = profile_hdr_targets(
        [Item(10, 5), Item(11, 0), Item(12, -1), Item(13, 6)],
        {
            10: {"dovi"},
            11: {"hdr10"},
            12: {"hdr10p"},
            13: {"hdr10", "hdr10p"},
        },
    )
    assert targets == {"dovi", "hdr10", "hdr10p"}


def test_preference_target_choices_expand_dovi_targets():
    assert preference_target_choices({"hdr", "dovi"}) == [
        "sdr",
        "hdr",
        "dovi_no_fallback",
        "dovi_fallback",
    ]
    assert preference_target_choices({"hdr10p"}) == ["sdr", "hdr10p"]


def test_default_profile_preference_prefers_base_hdr_then_dovi_fallback():
    assert default_profile_preference(["hdr", "dovi_no_fallback", "dovi_fallback"]) == (
        "hdr",
        "dovi_fallback",
    )
    assert default_profile_preference(["dovi_no_fallback", "dovi_fallback"]) == (
        "dovi_no_fallback",
        "dovi_fallback",
    )


def test_preference_status_resolves_meet_exceed_and_no_target():
    assert (
        preference_status(
            file_tags={"dovi", "hdr10"},
            profile_targets={"hdr", "dovi"},
            meet_target="hdr",
            exceed_target="dovi_fallback",
        )
        == "exceeds_target"
    )
    assert (
        preference_status(
            file_tags={"hdr10"},
            profile_targets={"hdr", "dovi"},
            meet_target="hdr",
            exceed_target="dovi_fallback",
        )
        == "meets_target"
    )
    assert (
        preference_status(
            file_tags={"dovi", "dovi_no_fallback"},
            profile_targets={"hdr", "dovi"},
            meet_target="hdr10",
            exceed_target="dovi_fallback",
        )
        == "meets_target"
    )
    assert (
        preference_status(
            file_tags={"hdr10"},
            profile_targets=set(),
            meet_target=None,
            exceed_target=None,
        )
        == "no_hdr_target"
    )


def test_is_valid_preference_pair_requires_stricter_exceed_target():
    assert is_valid_preference_pair("hdr", "dovi_fallback") is True
    assert is_valid_preference_pair("hdr10p", "dovi_no_fallback") is True
    assert is_valid_preference_pair("dovi_fallback", "hdr10p") is False
    assert is_valid_preference_pair(None, None) is True
    assert is_valid_preference_pair("hdr", None) is True
    assert is_valid_preference_pair(None, "dovi_fallback") is True


def test_ordered_tags_stable():
    assert ordered_tags({"dovi_no_fallback", "hdr10", "dovi"}) == [
        "hdr10",
        "dovi",
        "dovi_no_fallback",
    ]
