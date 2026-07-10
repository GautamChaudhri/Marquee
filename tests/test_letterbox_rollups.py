from __future__ import annotations

from types import SimpleNamespace

from marquee.api.routes.letterbox import _workflow_funnel_from_states
from marquee.core.letterbox_prefilter import prefilter_category_episode, tv_dimension_class
from marquee.core.letterbox_rollups import (
    EpisodeLetterbox,
    episode_bucket,
    season_rollup,
    show_rollup,
)
from marquee.models import LetterboxState


def _episode(
    episode_id: int,
    season_number: int,
    episode_number: int,
    *,
    status: str | None,
    aspect_label: str | None = None,
    bucket_override: str | None = None,
) -> EpisodeLetterbox:
    return EpisodeLetterbox(
        episode_id=episode_id,
        season_number=season_number,
        episode_number=episode_number,
        title=f"Episode {episode_number}",
        status=status,
        confidence="high" if status == "candidate" else None,
        aspect_label=aspect_label,
        recommended_crop_top=140 if status == "candidate" else None,
        recommended_crop_bottom=140 if status == "candidate" else None,
        applied_crop_top=140 if status in {"tagged", "reencoded"} else None,
        applied_crop_bottom=140 if status in {"tagged", "reencoded"} else None,
        eligible=True,
        reviewed=status == "tagged",
        bucket_override=bucket_override,
    )


def _episode_dims(width: int | None, height: int | None):
    return SimpleNamespace(video_width=width, video_height=height)


def test_episode_bucket_maps_statuses():
    assert episode_bucket("not_letterboxed") == "widescreen"
    assert episode_bucket("sampled_clear") == "sampled_widescreen"
    assert episode_bucket("candidate") == "candidate"
    assert episode_bucket("tagged") == "tagged"
    assert episode_bucket("reencoded") == "reencoded"
    assert episode_bucket("variable_unsafe") == "variable"
    assert episode_bucket("ineligible") == "ineligible"
    assert episode_bucket("errored") == "error"
    assert episode_bucket("prefilter_candidate") == "unanalyzed"


def test_tv_dimension_class_boundaries_and_prefilter_categories():
    assert tv_dimension_class(1835, 1080) == "pillarbox"
    assert tv_dimension_class(1836, 1080) is None
    assert tv_dimension_class(1933, 1080) is None
    assert tv_dimension_class(1944, 1080) == "open_matte"
    assert tv_dimension_class(1296, 720) == "open_matte"
    assert tv_dimension_class(1296, 719) is None
    assert tv_dimension_class(720, 480) is None
    assert tv_dimension_class(720, 576) is None
    assert tv_dimension_class(None, 1080) is None

    sd_category, sd_prefilter = prefilter_category_episode(_episode_dims(720, 480))
    assert sd_category == "candidate"
    assert sd_prefilter["bucket"] == "candidate"
    assert sd_prefilter["reason"] == "sd_assumed_candidate"

    pal_category, pal_prefilter = prefilter_category_episode(_episode_dims(720, 576))
    assert pal_category == "candidate"
    assert pal_prefilter["reason"] == "sd_assumed_candidate"

    missing_category, missing_prefilter = prefilter_category_episode(_episode_dims(None, 1080))
    assert missing_category == "unknown_resolution"
    assert missing_prefilter["bucket"] == "skip"


def test_verdict_precedence_and_known_content():
    cases = [
        (["candidate", "tagged"], "needs_action"),
        (["errored"], "needs_action"),
        (["tagged", "reencoded"], "treated"),
        (["not_letterboxed", "sampled_clear", "variable_unsafe"], "ok"),
        (["ineligible", "prefilter_candidate"], "unanalyzed"),
    ]

    for statuses, expected in cases:
        rollup = season_rollup(
            [_episode(index, 1, index, status=status) for index, status in enumerate(statuses, 1)]
        )
        assert rollup["verdict"] == expected


def test_content_types_fold_sampled_widescreen_and_order_by_count_then_type():
    rollup = season_rollup(
        [
            _episode(1, 1, 1, status="not_letterboxed"),
            _episode(2, 1, 2, status="not_letterboxed"),
            _episode(3, 1, 3, status="sampled_clear"),
            _episode(4, 1, 4, status=None, bucket_override="open_matte"),
            _episode(5, 1, 5, status=None, bucket_override="pillarbox"),
        ]
    )

    assert rollup["content_types"] == [
        {"type": "widescreen", "count": 3},
        {"type": "open_matte", "count": 1},
        {"type": "pillarbox", "count": 1},
    ]


def test_season_uniformity_has_no_vote_for_unknown_error_and_ineligible():
    rollup = season_rollup(
        [
            _episode(1, 1, 1, status="prefilter_candidate"),
            _episode(2, 1, 2, status="errored"),
            _episode(3, 1, 3, status="ineligible"),
        ]
    )

    assert rollup["uniformity"] is None


def test_season_uniformity_is_uniform_for_one_presentation_label():
    rollup = season_rollup(
        [
            _episode(1, 1, 1, status="not_letterboxed", aspect_label="1.78:1"),
            _episode(2, 1, 2, status="sampled_clear"),
        ]
    )

    assert rollup["uniformity"] == "uniform"
    assert rollup["dominant_aspect_label"] is None


def test_season_uniformity_variable_episode_forces_dirty_mixed():
    rollup = season_rollup(
        [
            _episode(1, 1, 1, status="candidate", aspect_label="2.39:1"),
            _episode(2, 1, 2, status="variable_unsafe"),
        ]
    )

    assert rollup["uniformity"] == "dirty_mixed"


def test_season_uniformity_mixed_presentation_labels_are_dirty_mixed():
    rollup = season_rollup(
        [
            _episode(1, 1, 1, status="candidate", aspect_label="2.39:1"),
            _episode(2, 1, 2, status="not_letterboxed"),
        ]
    )

    assert rollup["uniformity"] == "dirty_mixed"
    assert rollup["dominant_aspect_label"] == "2.39:1"


def test_show_uniformity_is_clean_mixed_for_different_uniform_content_types():
    pillarbox = season_rollup(
        [_episode(1, 1, 1, status=None, bucket_override="pillarbox")]
    )
    widescreen = season_rollup([_episode(2, 2, 1, status="not_letterboxed")])

    rollup = show_rollup({1: pillarbox, 2: widescreen})

    assert rollup["uniformity"] == "clean_mixed"
    assert rollup["content_types"] == [
        {"type": "widescreen", "count": 1},
        {"type": "pillarbox", "count": 1},
    ]


def test_show_uniformity_is_clean_mixed_for_letterboxed_and_widescreen_seasons():
    letterboxed = season_rollup([_episode(1, 1, 1, status="tagged", aspect_label="2.39:1")])
    widescreen = season_rollup([_episode(2, 2, 1, status="not_letterboxed")])

    assert show_rollup({1: letterboxed, 2: widescreen})["uniformity"] == "clean_mixed"


def test_show_rollup_excludes_specials():
    specials = season_rollup([_episode(1, 0, 1, status="candidate", aspect_label="2.39:1")])
    widescreen = season_rollup([_episode(2, 1, 1, status="not_letterboxed")])

    rollup = show_rollup({0: specials, 1: widescreen})

    assert rollup["episodes_total"] == 1
    assert rollup["bucket_counts"]["candidate"] == 0
    assert rollup["uniformity"] == "uniform"


def test_show_uniformity_is_null_without_voting_seasons():
    unknown = season_rollup([_episode(1, 1, 1, status="prefilter_candidate")])
    error = season_rollup([_episode(2, 2, 1, status="errored")])

    assert show_rollup({1: unknown, 2: error})["uniformity"] is None


def test_workflow_funnel_ignores_missing_states_but_counts_prefilter_rows():
    funnel = _workflow_funnel_from_states(
        [
            LetterboxState(media_type="movie", movie_id=1, status="prefilter_candidate"),
            LetterboxState(media_type="movie", movie_id=2, status="candidate"),
            LetterboxState(media_type="movie", movie_id=3, status="tagged", reviewed=False),
            LetterboxState(media_type="movie", movie_id=4, status="tagged", reviewed=True),
            None,
        ]
    )

    assert funnel == {
        "candidates": 1,
        "staging": 1,
        "preview": 1,
        "processed": 1,
    }
