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
    assert episode_bucket("not_letterboxed") == "clear"
    assert episode_bucket("sampled_clear") == "sampled_clear"
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


def test_season_rollup_marks_clean_sampled_and_uniform():
    rollup = season_rollup(
        [
            _episode(1, 1, 1, status="not_letterboxed"),
            _episode(2, 1, 2, status="sampled_clear"),
            _episode(3, 1, 3, status="not_letterboxed"),
        ]
    )

    assert rollup["verdict"] == "clean"
    assert rollup["uniformity"] == "uniform"
    assert rollup["bucket_counts"]["clear"] == 2
    assert rollup["bucket_counts"]["sampled_clear"] == 1


def test_season_rollup_handles_open_matte_and_pillarbox_verdicts():
    assert season_rollup([_episode(1, 1, 1, status=None, bucket_override="open_matte")])[
        "verdict"
    ] == "clean"
    assert season_rollup(
        [
            _episode(1, 1, 1, status=None, bucket_override="open_matte"),
            _episode(2, 1, 2, status=None, bucket_override="pillarbox"),
        ]
    )["verdict"] == "clean"
    assert season_rollup(
        [
            _episode(1, 1, 1, status=None, bucket_override="open_matte"),
            _episode(2, 1, 2, status="prefilter_candidate"),
        ]
    )["verdict"] == "unanalyzed"
    assert season_rollup(
        [
            _episode(1, 1, 1, status=None, bucket_override="open_matte"),
            _episode(2, 1, 2, status="not_letterboxed"),
        ]
    )["verdict"] == "clean"
    assert season_rollup(
        [
            _episode(1, 1, 1, status=None, bucket_override="open_matte"),
            _episode(2, 1, 2, status="candidate"),
        ]
    )["verdict"] == "needs_action"


def test_open_matte_and_pillarbox_do_not_affect_aspect_rollups():
    rollup = season_rollup(
        [
            _episode(1, 1, 1, status=None, aspect_label="2.40:1", bucket_override="open_matte"),
            _episode(2, 1, 2, status=None, aspect_label="1.33:1", bucket_override="pillarbox"),
        ]
    )

    assert rollup["dominant_aspect_label"] is None
    assert rollup["uniformity"] == "uniform"


def test_show_rollup_excludes_specials_and_reports_uniform_by_season():
    specials = season_rollup([_episode(1, 0, 1, status="candidate", aspect_label="2.40:1")])
    season_one = season_rollup([_episode(2, 1, 1, status="candidate", aspect_label="2.40:1")])
    season_two = season_rollup([_episode(3, 2, 1, status="candidate", aspect_label="1.85:1")])

    rollup = show_rollup({0: specials, 1: season_one, 2: season_two})

    assert rollup["verdict"] == "needs_action"
    assert rollup["uniformity"] == "uniform_by_season"
    assert rollup["episodes_total"] == 2
    assert rollup["bucket_counts"]["candidate"] == 2


def test_show_rollup_reports_treated_when_only_treated_and_clear():
    season_one = season_rollup(
        [
            _episode(1, 1, 1, status="reencoded", aspect_label="2.40:1"),
            _episode(2, 1, 2, status="not_letterboxed"),
        ]
    )

    rollup = show_rollup({1: season_one})

    assert rollup["verdict"] == "treated"
    assert rollup["uniformity"] == "uniform"


def test_season_rollup_ignores_clear_labels_for_dominant_and_uniformity():
    rollup = season_rollup(
        [
            _episode(1, 1, 1, status="candidate", aspect_label="2.35:1"),
            _episode(2, 1, 2, status="not_letterboxed", aspect_label="1.78:1"),
            _episode(3, 1, 3, status="not_letterboxed", aspect_label="1.78:1"),
        ]
    )

    assert rollup["dominant_aspect_label"] == "2.35:1"
    assert rollup["uniformity"] == "uniform"


def test_show_rollup_dominant_ignores_clean_season_labels():
    season_one = season_rollup(
        [
            _episode(1, 1, 1, status="candidate", aspect_label="2.35:1"),
            _episode(2, 1, 2, status="not_letterboxed", aspect_label="1.78:1"),
        ]
    )
    season_two = season_rollup(
        [
            _episode(3, 2, 1, status="not_letterboxed", aspect_label="1.78:1"),
            _episode(4, 2, 2, status="not_letterboxed", aspect_label="1.78:1"),
        ]
    )

    rollup = show_rollup({1: season_one, 2: season_two})

    assert rollup["dominant_aspect_label"] == "2.35:1"


def test_show_rollup_reports_unanalyzed_when_only_ineligible_and_prefilter_rows():
    season_one = season_rollup(
        [
            _episode(1, 1, 1, status="ineligible"),
            _episode(2, 1, 2, status="prefilter_candidate"),
        ]
    )

    rollup = show_rollup({1: season_one})

    assert rollup["verdict"] == "unanalyzed"
    assert rollup["bucket_counts"]["ineligible"] == 1
    assert rollup["bucket_counts"]["unanalyzed"] == 1


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
