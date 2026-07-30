from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from marquee.core.configuration import CONFIGURATION_CATALOG
from marquee.core.jobs.documents import (
    PosterPipelineGroupRequestV1,
    PosterPipelineGroupResultV1,
)
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.poster_group_limits import MAX_POSTER_GROUP_MEMBERS
from marquee.core.jobs.poster_pipeline import _STAGE_MAP, _pipeline_baseline_signature
from marquee.core.jobs.terminal_decision import JobOutcome
from marquee.core.pipeline_config import PipelineSettings, pipeline_settings
from marquee.core.pipeline_config_meta import KNOB_GROUPS, KNOB_META
from marquee.ml.residual import baseline_signature


def _movie(movie_id: int) -> dict[str, object]:
    return {"movie_id": movie_id, "title": f"Movie {movie_id}"}


def _series(series_id: int) -> dict[str, object]:
    return {"series_id": series_id, "title": f"Series {series_id}"}


def test_group_request_is_bounded_unique_and_library_homogeneous() -> None:
    request = PosterPipelineGroupRequestV1.model_validate(
        {"library": "movies", "chunk_index": 2, "members": [_movie(1), _movie(2)]}
    )
    assert len(request.members) == 2

    invalid_members = (
        [_movie(1), _movie(1)],
        [_movie(1), _series(2)],
        [{"episode_id": 1, "title": "Episode"}],
    )
    for members in invalid_members:
        with pytest.raises(ValidationError):
            PosterPipelineGroupRequestV1.model_validate(
                {"library": "movies", "chunk_index": 0, "members": members}
            )
    largest = PosterPipelineGroupRequestV1.model_validate(
        {
            "library": "movies",
            "chunk_index": 0,
            "batch_mode": "all_at_once",
            "members": [_movie(index) for index in range(1, MAX_POSTER_GROUP_MEMBERS + 1)],
        }
    )
    assert len(largest.members) == MAX_POSTER_GROUP_MEMBERS
    with pytest.raises(ValidationError):
        PosterPipelineGroupRequestV1.model_validate(
            {
                "library": "movies",
                "chunk_index": 0,
                "members": [
                    _movie(index) for index in range(1, MAX_POSTER_GROUP_MEMBERS + 2)
                ],
            }
        )


def test_group_result_requires_complete_projection_and_consistent_aggregate() -> None:
    result = PosterPipelineGroupResultV1(
        outcome="review_required",
        library="tv",
        chunk_index=1,
        member_count=2,
        succeeded_count=1,
        failed_count=1,
        projected_count=2,
        run_ids=("run-series", "run-season"),
        failed_subject_keys=("season:9",),
    )
    assert result.projected_count == result.member_count

    with pytest.raises(ValidationError, match="sum to member_count"):
        PosterPipelineGroupResultV1(
            library="movies",
            chunk_index=0,
            member_count=2,
            succeeded_count=1,
            projected_count=2,
            run_ids=("run-1", "run-2"),
        )
    with pytest.raises(ValidationError, match="one projection"):
        PosterPipelineGroupResultV1(
            library="movies",
            chunk_index=0,
            member_count=1,
            succeeded_count=1,
            projected_count=1,
            run_ids=("run-1", "run-2"),
        )


def test_group_definition_is_enabled_gpu_leaf_with_partial_success_alias() -> None:
    definition = JOB_DEFINITION_REGISTRY.get("poster_pipeline_group")
    assert definition.enabled is True
    assert definition.execution_class.value == "gpu"
    assert definition.subject_kinds == {"poster_subject_group"}
    assert definition.retry_mode.value == "domain_coordinated"
    assert dict(definition.terminal_policy.outcome_mapping)["review_required"] == (
        JobOutcome.PARTIALLY_SUCCEEDED
    )
    assert {
        "POSTER_GROUP_ENABLED",
        "POSTER_GROUP_BATCH_MODE",
        "POSTER_GROUP_CHUNK_SIZE",
    } <= definition.configuration_keys


def test_collecting_group_progress_maps_neutral_order_to_scoring() -> None:
    definition = JOB_DEFINITION_REGISTRY.get("poster_pipeline_group")

    assert _STAGE_MAP["neutral-order"] == "scoring"
    assert "scoring" in definition.progress_policy.stage_keys


def test_group_rollout_configuration_is_database_owned_and_hard_bounded() -> None:
    settings = PipelineSettings(
        POSTER_GROUP_ENABLED=True,
        POSTER_GROUP_BATCH_MODE="all_at_once",
        POSTER_GROUP_CHUNK_SIZE=16,
    )
    assert settings.POSTER_GROUP_ENABLED is True
    assert settings.POSTER_GROUP_BATCH_MODE == "all_at_once"
    assert settings.POSTER_GROUP_CHUNK_SIZE == 16
    for invalid in (0, 17):
        with pytest.raises(ValidationError, match="POSTER_GROUP_CHUNK_SIZE"):
            PipelineSettings(POSTER_GROUP_CHUNK_SIZE=invalid)

    for key in (
        "POSTER_GROUP_ENABLED",
        "POSTER_GROUP_BATCH_MODE",
        "POSTER_GROUP_CHUNK_SIZE",
    ):
        entry = CONFIGURATION_CATALOG[key]
        assert entry.owner == "pipeline"
        assert entry.database_owned is True
        assert entry.apply_mode == "next_job"
        assert key in KNOB_META
    downloads = next(group for group in KNOB_GROUPS if group["id"] == "downloads")
    assert {
        "POSTER_GROUP_ENABLED",
        "POSTER_GROUP_BATCH_MODE",
        "POSTER_GROUP_CHUNK_SIZE",
    } <= set(downloads["knobs"])


def test_runner_baseline_uses_the_sealed_execution_configuration() -> None:
    context = SimpleNamespace(configuration={"WEIGHT_KNN_SIM": 0.123})
    effective = pipeline_settings.model_copy(update=context.configuration)

    assert _pipeline_baseline_signature(context) == baseline_signature(
        effective.scorer_weights
    )
