from __future__ import annotations

import pytest
from pydantic import ValidationError

from marquee.core.jobs.contracts import (
    EffectSafety,
    MigrationState,
    ProgressStrategy,
    TriggerKind,
)
from marquee.core.jobs.documents import DocumentKind, UnsupportedDocumentVersionError
from marquee.core.jobs.inventory import (
    BUILTIN_JOB_TYPES,
    HEALING_TYPES,
    MEDIA_OPERATION_TYPES,
    PARENT_ONLY_TYPES,
    REGISTERED_HANDLER_TYPES,
    ROUTE_CONSTRUCTED_TYPES,
    SCHEDULE_PRODUCED_TYPES,
    WEBHOOK_RESERVED_TYPES,
)
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.subjects import (
    AggregateBatchSnapshot,
    EpisodeSnapshot,
    MaintenanceScopeSnapshot,
    MediaFileSnapshot,
    ModelProfileTrainingSnapshot,
    MovieSnapshot,
    PosterCandidateSetSnapshot,
    SeasonSnapshot,
    SeriesSnapshot,
    SystemWorkSnapshot,
    TrackSnapshot,
)


def _subjects():
    common = {"display_id": "stable:1", "display_name": "Stable subject"}
    return {
        "movie": MovieSnapshot(**common, movie_id=1, title="Movie"),
        "series": SeriesSnapshot(**common, series_id=1, series_title="Series"),
        "season": SeasonSnapshot(
            **common, season_id=1, series_id=1, series_title="Series", season_number=1
        ),
        "episode": EpisodeSnapshot(
            **common,
            episode_id=1,
            series_id=1,
            series_title="Series",
            season_number=1,
            episode_number=2,
            episode_code="S01E02",
        ),
        "media_file": MediaFileSnapshot(
            **common,
            media_file_id=1,
            file_name="video.mkv",
            media_kind="standalone",
            source="test",
            source_key="1",
        ),
        "track": TrackSnapshot(
            **common,
            track_kind="subtitle",
            media_file_id=1,
            embedded=True,
            file_name="video.mkv",
        ),
        "poster_candidate_set": PosterCandidateSetSnapshot(
            **common, media_kind="movie", subject_id=1, title="Movie"
        ),
        "model_profile_training": ModelProfileTrainingSnapshot(
            **common, subject_type="training", name="training"
        ),
        "aggregate_batch": AggregateBatchSnapshot(**common, batch_type="test"),
        "maintenance_scope": MaintenanceScopeSnapshot(**common, scope="test"),
        "system_work": SystemWorkSnapshot(**common, work="system_noop"),
    }


def test_manifest_has_exactly_one_definition_for_every_inventory_source() -> None:
    assert len(JOB_DEFINITION_REGISTRY) == 53
    assert JOB_DEFINITION_REGISTRY.types == BUILTIN_JOB_TYPES
    for inventory in (
        REGISTERED_HANDLER_TYPES,
        MEDIA_OPERATION_TYPES,
        PARENT_ONLY_TYPES,
        ROUTE_CONSTRUCTED_TYPES,
        HEALING_TYPES,
        SCHEDULE_PRODUCED_TYPES,
        WEBHOOK_RESERVED_TYPES,
    ):
        assert inventory <= JOB_DEFINITION_REGISTRY.types


def test_only_noop_is_enabled_and_webhook_stays_reserved_disabled() -> None:
    assert JOB_DEFINITION_REGISTRY.enabled_types == {
        "system_noop",
        "library_sync",
        "poster_pipeline",
        "poster_deploy",
        "poster_restore",
        "poster_reset",
        "poster_backup_subject",
        "backup_create",
        "poster_maintenance",
        "pipeline_cache_clear",
        "job_retention_purge",
        "system_metrics_purge",
        "letterbox_detect",
        "letterbox_detect_episode",
        "letterbox_detect_tv_scope",
        "subtitle_scan",
        "subtitle_policy_audit",
        "dovi_analyze",
        "learned_head_train",
        "poster_rescan",
        "taste_map",
        "taste_rebuild",
    }
    webhook = JOB_DEFINITION_REGISTRY.get("radarr_upgrade")
    assert webhook.trigger_kinds == {TriggerKind.WEBHOOK}
    assert not webhook.enabled


def test_all_documents_are_strict_current_v1_and_policy_is_not_client_input() -> None:
    forbidden = {
        "entrypoint",
        "timeout",
        "retry_policy",
        "effect_safety",
        "progress_policy",
        "actions",
    }
    valid_requests = {
        "subtitle_policy_audit": {
            "policy_id": 1,
            "policy_revision": 1,
            "policy_snapshot": {},
            "scope": "all",
        },
        "letterbox_detect": {"movie_id": 1, "media_file_id": 1},
        "letterbox_detect_episode": {"media_file_id": 1, "episode_ids": [1]},
            "letterbox_detect_tv_scope": {"series_id": 1},
        "dovi_analyze": {
                "media_file_id": 1,
                "movie_id": 1,
                "source_signature": "a" * 40,
        },
        "poster_pipeline": {"movie_id": 1, "title": "Example"},
        "poster_pipeline_batch": {"scope": "selected", "selection_count": 1},
        "poster_pipeline_tv_batch": {"scope": "series", "selection_count": 1},
        "taste_rebuild": {},
        "taste_map": {},
        "learned_head_train": {},
        "poster_rescan": {},
        "poster_deploy": {
            "target_kind": "movie",
            "target_id": 1,
            "candidate": {
                "source": "pipeline_run",
                "storage_key": "runs/example/candidate.jpg",
                "run_id": "run-example",
                "candidate_reference": "candidate.jpg",
                "expected_checksum": "a" * 64,
            },
        },
        "poster_restore": {"target_kind": "movie", "target_id": 1},
        "poster_reset": {"target_kind": "movie", "target_id": 1},
        "poster_backup_subject": {"target_kind": "movie", "target_id": 1},
        "poster_deploy_reset": {
            "operation": "reset",
            "scope": "all",
            "selection_count": 0,
        },
        "poster_backup_all": {
            "operation": "backup",
            "scope": "all",
            "selection_count": 0,
        },
        "poster_heal": {
            "operation": "heal",
            "scope": "missing",
            "selection_count": 0,
        },
        "backup_create": {},
        "poster_maintenance": {},
        "pipeline_cache_clear": {},
        "job_retention_purge": {"retention_days": 30},
        "system_metrics_purge": {"retention_days": 30},
    }
    valid_results = {
        "taste_rebuild": {
            "family": "taste_profile",
            "version": "v1-test",
            "checksum": "a" * 64,
            "expected_generation": 0,
            "active_generation": 1,
            "activated": True,
        },
        "taste_map": {
            "family": "taste_map",
            "version": "v1-test",
            "checksum": "a" * 64,
            "expected_generation": 0,
            "active_generation": 1,
            "activated": True,
        },
        "learned_head_train": {
            "family": "learned_head",
            "version": "v1-test",
            "checksum": "a" * 64,
            "expected_generation": 0,
            "active_generation": 1,
            "activated": True,
        },
        "poster_rescan": {"observed": 0, "changed": 0, "missing": 0},
    }
    mutation_result = {
        "outcome": "no_change",
        "reason_code": "already_identical",
        "message": "The poster already matches.",
        "requested_targets": [
            {
                "key": "movie:1:poster",
                "kind": "movie_artwork",
                "label": "Example",
                "operation": "poster_deploy",
            }
        ],
        "target_outcomes": [
            {
                "target": {
                    "key": "movie:1:poster",
                    "kind": "movie_artwork",
                    "label": "Example",
                    "operation": "poster_deploy",
                },
                "status": "skipped",
                "stage": "publishing",
                "reason_code": "already_identical",
                "message": "No bytes changed.",
                "bytes_changed": False,
                "product_state_changed": False,
            }
        ],
        "validation": {"verdict": "passed"},
        "atomicity": {
            "group_id": "poster:movie:1",
            "boundary": "single_target",
            "published": False,
            "rollback_available": True,
            "uncertain_state": False,
        },
    }
    for job_type in (
        "poster_deploy",
        "poster_restore",
        "poster_reset",
        "poster_backup_subject",
    ):
        valid_results[job_type] = mutation_result
    maintenance_result = {
        "outcome": "no_change",
        "message": "No eligible maintenance targets were found.",
        "plan_checksum": "a" * 64,
        "planned_count": 0,
        "processed_count": 0,
        "deleted_count": 0,
    }
    for job_type in (
        "backup_create",
        "poster_maintenance",
        "pipeline_cache_clear",
        "job_retention_purge",
        "system_metrics_purge",
    ):
        valid_results[job_type] = {**maintenance_result, "operation": job_type}
    for definition in JOB_DEFINITION_REGISTRY:
        assert definition.request.current_version == 1
        assert definition.result.current_version == 1
        assert definition.error.current_version == 1
        assert not forbidden & definition.request.models[1].model_fields.keys()
        definition.request.validate(valid_requests.get(definition.job_type, {}), version=1)
        definition.result.validate(valid_results.get(definition.job_type, {}), version=1)
        error = {"code": "test_failure", "summary": "Safe summary"}
        if definition.job_type in {
            "poster_deploy",
            "poster_restore",
            "poster_reset",
            "poster_backup_subject",
        }:
            error["stage"] = "execution"
        definition.error.validate(error, version=1)
        with pytest.raises(ValidationError):
            definition.request.validate({"timeout": 1}, version=1)
        with pytest.raises(UnsupportedDocumentVersionError):
            definition.request.validate({}, version=2)
        assert definition.request.kind == DocumentKind.REQUEST


def test_every_definition_has_explicit_subject_and_non_generic_presentation() -> None:
    subjects = _subjects()
    for definition in JOB_DEFINITION_REGISTRY:
        assert definition.presenter_key == f"jobs.{definition.job_type}"
        assert definition.subject_kinds
        for kind in definition.subject_kinds:
            assert definition.subject_builder(subjects[kind]).kind == kind


def test_progress_policies_are_complete_and_native_adapters_are_truthful() -> None:
    native = {
        definition.job_type: definition.progress_policy.tool_adapter
        for definition in JOB_DEFINITION_REGISTRY
        if definition.progress_policy.tool_adapter is not None
    }
    assert native == {
        "audio_remove": "mkvmerge_gui",
        "audio_reorder": "mkvmerge_gui",
            "dovi_convert": "ffmpeg_progress",
            "dovi_analyze": "ffprobe_dovi_tool",
        "letterbox_reencode": "ffmpeg_progress",
        "letterbox_detect": "ffprobe_ffmpeg_cropdetect",
        "letterbox_detect_episode": "ffprobe_ffmpeg_cropdetect",
        "letterbox_detect_tv_scope": "ffprobe_ffmpeg_cropdetect",
        "poster_pipeline": "poster_analysis_adapter",
        "poster_rescan": "bounded_filesystem_observation",
        "subtitle_embed": "mkvmerge_gui",
        "subtitle_extract": "mkvmerge_gui",
        "subtitle_metadata": "mkvmerge_gui",
        "subtitle_policy": "mkvmerge_gui",
        "subtitle_remove": "mkvmerge_gui",
        "subtitle_restore": "mkvmerge_gui",
        "track_remove": "mkvmerge_gui",
        "taste_rebuild": "immutable_ml_publication",
        "taste_map": "immutable_ml_publication",
        "learned_head_train": "immutable_ml_publication",
    }
    for definition in JOB_DEFINITION_REGISTRY:
        policy = definition.progress_policy
        assert policy.stage_keys
        if definition.job_type == "system_noop":
            assert policy.strategy == ProgressStrategy.NONE
        else:
            assert policy.strategy != ProgressStrategy.NONE


def test_unsafe_mutations_have_one_attempt_and_no_retry_exceptions() -> None:
    unsafe = [
        definition
        for definition in JOB_DEFINITION_REGISTRY
        if definition.effect_safety == EffectSafety.UNSAFE_MUTATION
    ]
    assert unsafe
    assert all(definition.retry_policy.max_attempts == 1 for definition in unsafe)
    assert all(definition.retry_policy.idempotency_proof is None for definition in unsafe)


def test_parent_definitions_have_fixed_sealed_children_and_known_child_types() -> None:
    for definition in JOB_DEFINITION_REGISTRY:
        if definition.job_type in PARENT_ONLY_TYPES:
            assert definition.migration_state == MigrationState.PARENT_ONLY
            assert definition.parent_policy.fixed_children
            assert definition.parent_policy.require_sealed
            assert definition.child_job_types <= BUILTIN_JOB_TYPES
        else:
            assert definition.parent_policy is None
            assert not definition.child_job_types


def test_configuration_dependencies_and_action_policies_are_bounded() -> None:
    for definition in JOB_DEFINITION_REGISTRY:
        assert len(definition.configuration_keys) <= 128
        assert definition.action_policy.detail
        assert not definition.action_policy.pause
