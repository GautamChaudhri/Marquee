"""Unit tests for Radarr overlay HDR / target helpers."""

from marquee.core.radarr_overlay import (
    classify_custom_format_tags,
    classify_hdr_tags,
    hdr_target_status,
    ordered_tags,
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


def test_hdr_target_status_respects_dovi_fallback_toggle():
    assert hdr_target_status(
        has_file=True,
        file_tags={"dovi", "dovi_no_fallback"},
        targets={"dovi"},
        require_dovi_fallback=True,
    ) == "below_target"
    assert hdr_target_status(
        has_file=True,
        file_tags={"dovi", "dovi_no_fallback"},
        targets={"dovi"},
        require_dovi_fallback=False,
    ) == "met_target"
    assert hdr_target_status(
        has_file=False,
        file_tags=set(),
        targets={"hdr10"},
        require_dovi_fallback=True,
    ) == "no_file"
    assert hdr_target_status(
        has_file=True,
        file_tags={"hdr10"},
        targets=set(),
        require_dovi_fallback=True,
    ) == "no_hdr_target"


def test_ordered_tags_stable():
    assert ordered_tags({"dovi_no_fallback", "hdr10", "dovi"}) == [
        "hdr10",
        "dovi",
        "dovi_no_fallback",
    ]
