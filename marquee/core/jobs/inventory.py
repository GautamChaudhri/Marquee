"""Frozen JMC2B built-in inventory regenerated from current source.

These sets describe known work; they do not enable dispatch.  Coverage tests
compare them with decorators, route constructors, media-operation call sites,
and parent constructors so drift fails loudly.
"""

REGISTERED_HANDLER_TYPES: frozenset[str] = frozenset()

MEDIA_OPERATION_TYPES = frozenset(
    {
        "audio_remove",
        "audio_reorder",
        "dovi_convert",
        "dovi_discard",
        "dovi_publish",
        "dovi_restore",
        "letterbox_reencode",
        "letterbox_reencode_discard",
        "letterbox_reencode_publish",
        "letterbox_reencode_restore",
        "subtitle_embed",
        "subtitle_extract",
        "subtitle_generate",
        "subtitle_metadata",
        "subtitle_policy",
        "subtitle_remove",
        "subtitle_restore",
        "subtitle_scan",
        "track_remove",
    }
)

PARENT_ONLY_TYPES = frozenset(
    {
        "audio_subs_deep_scan",
        "dovi_analyze_batch",
        "letterbox_apply_batch",
        "letterbox_apply_tv_scope",
        "letterbox_detect_batch",
        "letterbox_detect_tv_batch",
        "letterbox_reencode_tv_batch",
        "letterbox_reencode_publish_batch",
        "letterbox_heal",
        "letterbox_revert_tv_scope",
        "poster_pipeline_batch",
        "poster_pipeline_tv_batch",
        "poster_backup_all",
        "poster_deploy_reset",
        "poster_heal",
        "subtitle_generate_batch",
        "subtitle_policy_batch",
        "subtitle_scan_all",
    }
)

# Canonical read-only job types introduced by JMC4B that have no legacy ``@register`` handler
# (so they are not REGISTERED_HANDLER_TYPES) and are not per-track media operations.
CANONICAL_READ_ONLY_TYPES = frozenset(
    {
        "letterbox_detect",
        "letterbox_detect_episode",
        "letterbox_detect_tv_scope",
        "library_sync",
        "dovi_analyze",
        "ranking_residual_train",
        "poster_pipeline",
        "poster_rescan",
        "subtitle_policy_audit",
        "taste_map",
        "taste_enrich",
        "taste_rebuild",
        "system_noop",
    }
)

RESERVED_TYPES = frozenset({"radarr_upgrade"})

CANONICAL_MUTATION_TYPES = frozenset(
    {
        "letterbox_apply",
        "letterbox_remove",
        "poster_deploy",
        "poster_restore",
        "poster_reset",
        "poster_backup_subject",
    }
)

# Canonical exclusive-maintenance definitions migrated in JMC5A. They remain built-in
# inventory entries after their legacy ``@register`` handlers are retired.
CANONICAL_MAINTENANCE_TYPES = frozenset(
    {
        "backup_create",
        "job_retention_purge",
        "pipeline_cache_clear",
        "poster_maintenance",
        "system_metrics_purge",
    }
)

BUILTIN_JOB_TYPES = (
    REGISTERED_HANDLER_TYPES
    | MEDIA_OPERATION_TYPES
    | PARENT_ONLY_TYPES
    | CANONICAL_READ_ONLY_TYPES
    | CANONICAL_MUTATION_TYPES
    | CANONICAL_MAINTENANCE_TYPES
    | RESERVED_TYPES
)

ROUTE_CONSTRUCTED_TYPES = frozenset(
    {
        "backup_create",
        "dovi_analyze",
        "dovi_analyze_batch",
        "dovi_convert",
        "dovi_discard",
        "dovi_publish",
        "dovi_restore",
        "ranking_residual_train",
        "letterbox_apply",
        "letterbox_apply_batch",
        "letterbox_apply_tv_scope",
        "letterbox_detect",
        "letterbox_detect_batch",
        "letterbox_detect_tv_scope",
        "letterbox_detect_tv_batch",
        "letterbox_heal",
        "letterbox_reencode",
        "letterbox_reencode_discard",
        "letterbox_reencode_publish",
        "letterbox_reencode_publish_batch",
        "letterbox_reencode_restore",
        "letterbox_reencode_tv_batch",
        "letterbox_remove",
        "letterbox_revert_tv_scope",
        "library_sync",
        "pipeline_cache_clear",
        "poster_backup_all",
        "poster_deploy",
        "poster_deploy_reset",
        "poster_heal",
        "poster_maintenance",
        "poster_pipeline",
        "poster_pipeline_batch",
        "poster_pipeline_tv_batch",
        "poster_rescan",
        "poster_reset",
        "subtitle_generate_batch",
        "subtitle_policy_audit",
        "subtitle_scan",
        "subtitle_scan_all",
        "taste_map",
        "taste_enrich",
    }
)

HEALING_TYPES = frozenset({"letterbox_heal", "poster_heal"})
SCHEDULE_PRODUCED_TYPES = frozenset(
    {"audio_subs_deep_scan", "job_retention_purge", "library_sync", "poster_heal"}
)
WEBHOOK_RESERVED_TYPES = frozenset({"radarr_upgrade"})
