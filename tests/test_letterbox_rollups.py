from __future__ import annotations

from marquee.api.routes.letterbox import _workflow_funnel_from_states
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
    )


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
