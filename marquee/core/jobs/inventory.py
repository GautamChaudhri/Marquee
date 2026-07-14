"""Frozen JMC2B built-in inventory regenerated from current source.

These sets describe known work; they do not enable dispatch.  Coverage tests
compare them with decorators, route constructors, media-operation call sites,
and parent constructors so drift fails loudly.
"""

REGISTERED_HANDLER_TYPES = frozenset(
    {
        "backup_create",
        "dovi_convert",
        "job_retention_purge",
        "letterbox_apply",
        "letterbox_apply_tv_scope",
        "letterbox_heal",
        "letterbox_remove",
        "letterbox_revert_tv_scope",
        "library_sync",
        "pipeline_cache_clear",
        "poster_backup_all",
        "poster_deploy_reset",
        "poster_heal",
        "poster_maintenance",
        "radarr_upgrade",
        "system_metrics_purge",
        "system_noop",
    }
)

MEDIA_OPERATION_TYPES = frozenset(
    {
        "audio_remove",
        "audio_reorder",
        "letterbox_reencode",
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
        "letterbox_detect_batch",
        "letterbox_detect_tv_batch",
        "letterbox_reencode_tv_batch",
        "poster_pipeline_batch",
        "poster_pipeline_tv_batch",
        "subtitle_generate_batch",
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
        "dovi_analyze",
        "learned_head_train",
        "poster_pipeline",
        "poster_rescan",
        "subtitle_policy_audit",
        "taste_map",
        "taste_rebuild",
    }
)

BUILTIN_JOB_TYPES = (
    REGISTERED_HANDLER_TYPES
    | MEDIA_OPERATION_TYPES
    | PARENT_ONLY_TYPES
    | CANONICAL_READ_ONLY_TYPES
)

ROUTE_CONSTRUCTED_TYPES = frozenset(
    {
        "backup_create",
        "dovi_analyze",
        "dovi_analyze_batch",
        "dovi_convert",
        "learned_head_train",
        "letterbox_apply",
        "letterbox_apply_batch",
        "letterbox_apply_tv_scope",
        "letterbox_detect",
        "letterbox_detect_batch",
        "letterbox_detect_tv_scope",
        "letterbox_detect_tv_batch",
        "letterbox_heal",
        "letterbox_reencode_tv_batch",
        "letterbox_remove",
        "letterbox_revert_tv_scope",
        "library_sync",
        "pipeline_cache_clear",
        "poster_backup_all",
        "poster_deploy_reset",
        "poster_heal",
        "poster_maintenance",
        "poster_pipeline",
        "poster_pipeline_batch",
        "poster_pipeline_tv_batch",
        "poster_rescan",
        "radarr_upgrade",
        "subtitle_generate_batch",
        "subtitle_policy_audit",
        "subtitle_scan_all",
        "taste_map",
        "taste_rebuild",
    }
)

HEALING_TYPES = frozenset({"letterbox_heal", "poster_heal"})
SCHEDULE_PRODUCED_TYPES = frozenset(
    {"audio_subs_deep_scan", "library_sync", "poster_heal"}
)
WEBHOOK_RESERVED_TYPES = frozenset({"radarr_upgrade"})
