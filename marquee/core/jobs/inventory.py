"""Frozen JMC2B built-in inventory regenerated from current source.

These sets describe known work; they do not enable dispatch.  Coverage tests
compare them with decorators, route constructors, media-operation call sites,
and parent constructors so drift fails loudly.
"""

REGISTERED_HANDLER_TYPES: frozenset[str] = frozenset()

MEDIA_OPERATION_TYPES = frozenset(
    {
    }
)

PARENT_ONLY_TYPES = frozenset(
    {
        "poster_pipeline_batch",
        "poster_pipeline_tv_batch",
        "poster_backup_all",
        "poster_deploy_reset",
        "poster_heal",
    }
)

# Canonical read-only job types introduced by JMC4B that have no legacy ``@register`` handler
# (so they are not REGISTERED_HANDLER_TYPES) and are not per-track media operations.
CANONICAL_READ_ONLY_TYPES = frozenset(
    {
        "library_sync",
        "ranking_residual_train",
        "poster_pipeline",
        "poster_rescan",
        "taste_map",
        "taste_enrich",
        "taste_rebuild",
        "system_noop",
    }
)

RESERVED_TYPES = frozenset({"radarr_upgrade"})

CANONICAL_MUTATION_TYPES = frozenset(
    {
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
        "ranking_residual_train",
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
        "taste_map",
        "taste_enrich",
    }
)

HEALING_TYPES = frozenset({"letterbox_heal", "poster_heal"})
SCHEDULE_PRODUCED_TYPES = frozenset(
    {"job_retention_purge", "library_sync", "poster_heal"}
)
WEBHOOK_RESERVED_TYPES = frozenset({"radarr_upgrade"})
