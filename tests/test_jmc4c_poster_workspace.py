"""C1 contract tests for confined, non-deploying poster-analysis work."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from marquee.core.jobs.delivery import EXECUTION_HANDLERS
from marquee.core.jobs.documents import PosterPipelineRequestV1, PosterPipelineResultV1
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY


def test_poster_request_is_path_free_and_server_policy_only() -> None:
    valid = {
        "movie_id": 1,
        "title": "Example",
        "source_descriptors": [{"provider": "tmdb", "reference": "movie:1"}],
    }
    request = PosterPipelineRequestV1.model_validate(valid)
    assert request.source_descriptors[0].reference == "movie:1"

    for invalid in (
        {**valid, "source_path": "/library/poster.jpg"},
        {**valid, "gpu": 1},
        {**valid, "artifact_key": "caller-selected"},
        {**valid, "source_descriptors": [{"provider": "tmdb", "reference": "/tmp/poster"}]},
    ):
        with pytest.raises(ValidationError):
            PosterPipelineRequestV1.model_validate(invalid)


def test_poster_result_has_no_artwork_mutation_claim() -> None:
    result = PosterPipelineResultV1(
        subject_label="Example", source_count=0, candidate_count=0, accepted_count=0
    )
    payload = result.model_dump(mode="json")
    assert not {"deployed", "deployment", "reset", "restore", "active_poster"} & set(payload)


def test_c2_pipeline_is_enabled_only_through_the_canonical_read_only_executor() -> None:
    definition = JOB_DEFINITION_REGISTRY.get("poster_pipeline")
    assert definition.enabled is True
    assert definition.effect_safety.value == "read_only"
    assert definition.execution_class.value == "gpu"
    assert definition.request.models[1] is PosterPipelineRequestV1
    assert "poster_pipeline" in EXECUTION_HANDLERS


def test_c1_execution_module_has_no_legacy_or_artwork_writer_bridge() -> None:
    source = Path("marquee/core/jobs/poster_pipeline.py").read_text()
    forbidden = (
        "run_manager",
        "progress_bridge",
        "job_manager",
        "media_job_manager",
        "cancel_registry",
    )
    assert all(token not in source for token in forbidden)
    assert "from marquee.pipeline" not in source
    assert "artifact_registry" not in source
    assert "register_physical_artifact" in source
    assert "workspace.staging_file" in source
