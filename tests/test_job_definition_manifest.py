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
    assert len(JOB_DEFINITION_REGISTRY) == 62  # +JMC6E dedicated taste enrichment
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
        "letterbox_apply",
            "letterbox_remove",
            "letterbox_reencode",
            "letterbox_reencode_publish",
            "letterbox_reencode_restore",
            "letterbox_reencode_discard",
        "letterbox_detect",
        "letterbox_detect_episode",
        "letterbox_detect_tv_scope",
        "subtitle_scan",
        "subtitle_policy_audit",
        "dovi_analyze",
        "dovi_convert",
        "dovi_publish",
        "dovi_restore",
        "dovi_discard",
        "ranking_residual_train",
        "poster_rescan",
        "taste_map",
        "taste_enrich",
        "taste_rebuild",
        # JMC5B B2: audio/subtitle removals, reorder, and metadata.
        "audio_remove",
        "track_remove",
        "subtitle_remove",
        "audio_reorder",
        "subtitle_metadata",
        "subtitle_extract",
        "subtitle_embed",
        "subtitle_generate",
        "subtitle_policy",
        "subtitle_restore",
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
            "dovi_convert": {
                "media_file_id": 1,
                "movie_id": 1,
                "kind": "p5_to_p81",
                "source_signature": "sha256:source",
                "source_size_bytes": 1000,
                "source_probe": {
                    "codec": "hevc", "width": 3840, "height": 2160,
                    "has_hdr": True, "has_dolby_vision": True,
                    "video_streams": 1, "audio_streams": 1,
                    "subtitle_streams": 0, "attachment_streams": 0,
                    "dovi_profile": 5, "enhancement_layer_present": False,
                    "bl_signal_compatibility_id": 0,
                },
            },
            "dovi_publish": {
                "media_file_id": 1, "movie_id": 1,
                "candidate_artifact_id": 1, "candidate_job_id": "candidate-job",
                "candidate_checksum": "a" * 64, "candidate_size_bytes": 1000,
                "expected_source_signature": "sha256:source",
                "source_probe": {
                    "codec": "hevc", "width": 3840, "height": 2160,
                    "has_hdr": True, "has_dolby_vision": True,
                    "video_streams": 1, "audio_streams": 1,
                    "subtitle_streams": 0, "attachment_streams": 0,
                    "dovi_profile": 5,
                },
                "candidate_probe": {
                    "codec": "hevc", "width": 3840, "height": 2160,
                    "has_hdr": True, "has_dolby_vision": True,
                    "video_streams": 1, "audio_streams": 1,
                    "subtitle_streams": 0, "attachment_streams": 0,
                    "dovi_profile": 8, "enhancement_layer_present": False,
                    "bl_signal_compatibility_id": 1,
                },
            },
            "dovi_restore": {
                "media_file_id": 1, "movie_id": 1, "candidate_artifact_id": 1,
                "backup_artifact_id": 2, "backup_checksum": "b" * 64,
                "backup_size_bytes": 1000,
                "expected_destination_signature": "sha256:candidate",
                "published_checksum": "a" * 64,
            },
            "dovi_discard": {
                "media_file_id": 1, "movie_id": 1, "candidate_artifact_id": 1,
                "candidate_checksum": "a" * 64,
            },
        "poster_pipeline": {"movie_id": 1, "title": "Example"},
        "poster_pipeline_batch": {"scope": "selected", "selection_count": 1},
        "poster_pipeline_tv_batch": {"scope": "series", "selection_count": 1},
        "taste_rebuild": {"expected_generation": 0},
        "taste_map": {},
        "taste_enrich": {},
        "ranking_residual_train": {},
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
        "audio_remove": {"media_file_id": 1, "selectors": [{
                "track_key": "audio:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:0",
                "facts": {
                    "kind": "audio",
                    "source": "embedded",
                    "language_tag": "en",
                    "codec": "ac3",
                    "channels": 6,
                },
                "inventory_signature": "sha256:example",
            }]},
        "track_remove": {"media_file_id": 1, "selectors": [{
                "track_key": "audio:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:0",
                "facts": {
                    "kind": "audio",
                    "source": "embedded",
                    "language_tag": "en",
                    "codec": "ac3",
                    "channels": 6,
                },
                "inventory_signature": "sha256:example",
            }]},
        "subtitle_remove": {"media_file_id": 1, "selectors": [{
                "track_key": "audio:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:0",
                "facts": {
                    "kind": "audio",
                    "source": "embedded",
                    "language_tag": "en",
                    "codec": "ac3",
                    "channels": 6,
                },
                "inventory_signature": "sha256:example",
            }]},
        "audio_reorder": {"media_file_id": 1, "ordered_selectors": [{
                "track_key": "audio:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:0",
                "facts": {
                    "kind": "audio",
                    "source": "embedded",
                    "language_tag": "en",
                    "codec": "ac3",
                    "channels": 6,
                },
                "inventory_signature": "sha256:example",
            }, {
                "track_key": "audio:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb:0",
                "facts": {
                    "kind": "audio",
                    "source": "embedded",
                    "language_tag": "fr",
                    "codec": "ac3",
                    "channels": 6,
                },
                "inventory_signature": "sha256:example",
            }]},
        "subtitle_metadata": {"media_file_id": 1, "edits": [{"selector": {
                "track_key": "subtitle:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:0",
                "facts": {
                    "kind": "subtitle",
                    "source": "embedded",
                    "language_tag": "en",
                    "codec": "subrip",
                    },
                "inventory_signature": "sha256:example",
            }, "is_forced": True}]},
        "subtitle_extract": {"media_file_id": 1, "selector": {
                "track_key": "subtitle:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:0",
                "facts": {
                    "kind": "subtitle",
                    "source": "embedded",
                    "language_tag": "en",
                    "codec": "subrip",
                },
                "inventory_signature": "sha256:example",
            }},
        "subtitle_embed": {"media_file_id": 1, "managed_asset_id": "asset-1", "language_tag": "en"},
        "subtitle_generate": {"media_file_id": 1, "language_tag": "en"},
        "subtitle_policy": {"media_file_id": 1, "policy_id": 1, "policy_revision": 1},
        "subtitle_restore": {
            "media_file_id": 1,
            "artifact_key": "jmc5/media-backups/mf-1/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.mkv",
            "checksum": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "source_signature": "sha256:src:1",
            "expected_destination_signature": "sha256:dst:1",
        },
        "subtitle_policy_batch": {"policy_id": 1, "policy_revision": 1},
        "backup_create": {},
        "poster_maintenance": {},
        "pipeline_cache_clear": {},
        "job_retention_purge": {"retention_days": 30},
        "system_metrics_purge": {"retention_days": 30},
        "letterbox_apply": {
            "media_file_id": 1,
            "subject_kind": "movie",
            "subject_ids": [1],
            "before": {
                "source_signature": "sha256:source",
                "status": "candidate",
                "confidence": "high",
                "variable_ar": False,
                "source_width": 1920,
                "source_height": 1080,
                "recommended_crop_top": 140,
                "recommended_crop_bottom": 140,
            },
            "crop_top": 140,
            "crop_bottom": 140,
        },
        "letterbox_remove": {
            "media_file_id": 1,
            "subject_kind": "movie",
            "subject_ids": [1],
            "before": {
                "source_signature": "sha256:source",
                "status": "tagged",
                "confidence": "high",
                "variable_ar": False,
                "source_width": 1920,
                "source_height": 1080,
                "current_crop_top": 140,
                "current_crop_bottom": 140,
                "recommended_crop_top": 140,
                "recommended_crop_bottom": 140,
            },
        },
        "letterbox_heal": {"operation": "heal_apply", "sealed_file_count": 0},
        "letterbox_apply_tv_scope": {"operation": "apply", "sealed_file_count": 0},
        "letterbox_revert_tv_scope": {"operation": "remove", "sealed_file_count": 0},
        "letterbox_apply_batch": {"operation": "apply", "sealed_file_count": 0},
            "letterbox_reencode": {
            "media_file_id": 1,
            "subject_kind": "movie",
            "subject_id": 1,
            "crop_top": 140,
            "crop_bottom": 140,
            "output_height": 800,
            "source": {
                "signature": "sha256:source",
                "size_bytes": 1000,
                "codec": "h264",
                "width": 1920,
                "height": 1080,
                "duration_seconds": 60.0,
                "pixel_format": "yuv420p",
                "has_hdr": False,
                "has_dolby_vision": False,
                "video_streams": 1,
                "audio_streams": 1,
                "subtitle_streams": 0,
                "attachment_streams": 0,
            },
            "encoder": {
                "codec": "h264",
                "encoder": "libx264",
                "family": "cpu",
                "quality": 23,
                "used_cpu_fallback": True,
                },
            },
            "letterbox_reencode_publish": {
                "media_file_id": 1,
                "subject_kind": "movie",
                "subject_id": 1,
                "candidate_artifact_id": 1,
                "candidate_job_id": "candidate-job",
                "candidate_checksum": "a" * 64,
                "candidate_size_bytes": 1000,
                "expected_source_signature": "sha256:source",
                "crop_top": 140,
                "crop_bottom": 140,
                "source_probe": {
                    "codec": "h264", "width": 1920, "height": 1080,
                    "duration_seconds": 60.0, "has_hdr": False,
                    "has_dolby_vision": False, "video_streams": 1,
                    "audio_streams": 1, "subtitle_streams": 0,
                    "attachment_streams": 0,
                },
                "candidate_probe": {
                    "codec": "h264", "width": 1920, "height": 800,
                    "duration_seconds": 60.0, "has_hdr": False,
                    "has_dolby_vision": False, "video_streams": 1,
                    "audio_streams": 1, "subtitle_streams": 0,
                    "attachment_streams": 0,
                },
            },
            "letterbox_reencode_restore": {
                "media_file_id": 1,
                "subject_kind": "movie",
                "subject_id": 1,
                "candidate_artifact_id": 1,
                "backup_artifact_id": 2,
                "backup_checksum": "b" * 64,
                "backup_size_bytes": 1000,
                "expected_destination_signature": "sha256:candidate",
                "published_checksum": "a" * 64,
            },
            "letterbox_reencode_discard": {
                "media_file_id": 1,
                "subject_kind": "movie",
                "subject_id": 1,
                "candidate_artifact_id": 1,
                "candidate_checksum": "a" * 64,
            },
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
        "taste_enrich": {
            "family": "taste_profile",
            "version": "v1-test",
            "checksum": "a" * 64,
            "expected_generation": 0,
            "active_generation": 1,
            "activated": True,
        },
        "ranking_residual_train": {
            "family": "ranking_residual",
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
    for job_type in ("letterbox_apply", "letterbox_remove"):
        valid_results[job_type] = mutation_result
    track_result = {**mutation_result, "before_inventory": {"signature": "sha256:example", "container": "matroska", "entries": []}}
    for job_type in (
        "audio_remove",
        "track_remove",
        "subtitle_remove",
        "audio_reorder",
        "subtitle_metadata",
        "subtitle_embed",
        "subtitle_policy",
        "subtitle_restore",
    ):
        valid_results[job_type] = track_result
    valid_results["subtitle_extract"] = {**mutation_result, "before_inventory": {"signature": "sha256:example", "container": "matroska", "entries": []}}
    valid_results["subtitle_generate"] = {
        **mutation_result,
        "before_inventory": {
            "signature": "sha256:example",
            "container": "matroska",
            "entries": [],
        },
        "provider": "subgen",
        "generation_atomicity": {
            "group_id": "subtitle-generate:media-file:1",
            "boundary": "single_target",
            "published": False,
            "rollback_available": True,
            "uncertain_state": False,
        },
    }
    valid_results["letterbox_reencode"] = {
        "outcome": "failed",
        "reason_code": "test",
        "message": "Typed failure evidence.",
        "crop_top": 140,
        "crop_bottom": 140,
        "encoder": "libx264",
        "hardware_family": "cpu",
        "used_cpu_fallback": True,
    }
    for operation in ("publish", "restore", "discard"):
        valid_results[f"letterbox_reencode_{operation}"] = {
            "outcome": "failed",
            "operation": operation,
            "reason_code": "test",
            "message": "Typed failure evidence.",
            "media_file_id": 1,
            "candidate_artifact_id": 1,
        }
        valid_results[f"dovi_{operation}"] = {
            "outcome": "failed",
            "operation": operation,
            "reason_code": "test",
            "message": "Typed failure evidence.",
            "media_file_id": 1,
            "movie_id": 1,
            "candidate_artifact_id": 1,
        }
    valid_results["dovi_convert"] = {
        "outcome": "failed",
        "reason_code": "test",
        "message": "Typed failure evidence.",
        "kind": "p5_to_p81",
    }
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
        "taste_enrich": "immutable_ml_publication",
        "ranking_residual_train": "immutable_ml_publication",
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
