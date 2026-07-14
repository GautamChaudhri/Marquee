"""C5 cross-family certification for the completed non-mutating Chunk 4 surface."""

from __future__ import annotations

from marquee.core.jobs.delivery import EXECUTION_HANDLERS
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.schedules import PRODUCTION_SCHEDULE_CATALOG

ENABLED_LEAVES = {
    "dovi_analyze",
    "learned_head_train",
    "letterbox_detect",
    "letterbox_detect_episode",
    "letterbox_detect_tv_scope",
    "library_sync",
    "poster_pipeline",
    "poster_rescan",
    "subtitle_policy_audit",
    "subtitle_scan",
    "system_noop",
    "taste_map",
    "taste_rebuild",
}

C15_DEFERRED = {
    "audio_remove",
    "audio_reorder",
    "backup_create",
    "dovi_convert",
    "job_retention_purge",
    "letterbox_apply",
    "letterbox_apply_tv_scope",
    "letterbox_heal",
    "letterbox_reencode",
    "letterbox_remove",
    "letterbox_revert_tv_scope",
    "pipeline_cache_clear",
    "poster_backup_all",
    "poster_deploy_reset",
    "poster_heal",
    "poster_maintenance",
    "radarr_upgrade",
    "subtitle_embed",
    "subtitle_extract",
    "subtitle_generate",
    "subtitle_metadata",
    "subtitle_policy",
    "subtitle_remove",
    "subtitle_restore",
    "system_metrics_purge",
    "track_remove",
}


def test_final_enabled_manifest_has_exactly_one_executor_per_leaf() -> None:
    assert JOB_DEFINITION_REGISTRY.enabled_types == ENABLED_LEAVES
    assert set(EXECUTION_HANDLERS) == ENABLED_LEAVES


def test_c15_mutating_and_operator_owned_manifest_is_dispatch_disabled() -> None:
    assert {definition.job_type for definition in JOB_DEFINITION_REGISTRY} >= C15_DEFERRED
    assert all(not JOB_DEFINITION_REGISTRY.get(job_type).enabled for job_type in C15_DEFERRED)
    assert all(
        not definition.enabled
        for definition in JOB_DEFINITION_REGISTRY
        if definition.execution_class.value == "media_write"
    )


def test_enabled_chunk_four_is_read_only_and_parent_batches_are_ticketless() -> None:
    assert all(
        JOB_DEFINITION_REGISTRY.get(job_type).effect_safety.value == "read_only"
        for job_type in ENABLED_LEAVES - {"system_noop"}
    )
    for job_type in ("poster_pipeline_batch", "poster_pipeline_tv_batch"):
        definition = JOB_DEFINITION_REGISTRY.get(job_type)
        assert definition.parent_policy is not None
        assert definition.entrypoint == "control"
        assert job_type not in EXECUTION_HANDLERS


def test_product_schedule_catalog_remains_exact_and_nonmutating() -> None:
    schedules = {definition.key: definition for definition in PRODUCTION_SCHEDULE_CATALOG}
    assert list(schedules) == ["library-sync", "audio-subs-deep-scan"]
    assert schedules["library-sync"].produced_job_type == "library_sync"
    assert schedules["audio-subs-deep-scan"].produced_job_type == "audio_subs_deep_scan"
    assert all(
        JOB_DEFINITION_REGISTRY.get(definition.produced_job_type).execution_class.value
        != "media_write"
        for definition in schedules.values()
    )
