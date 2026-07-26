"""Human-friendly labels for durable job and media-job types."""

from __future__ import annotations

JOB_LABELS: dict[str, str] = {
    "backup_create": "Backup Creation",
    "job_retention_purge": "Job Retention Cleanup",
    "ranking_residual_train": "Residual Preference Training",
    "library_sync": "Library Sync",
    "pipeline_cache_clear": "Pipeline Cache Clear",
    "poster_backup_all": "Poster Backup (All)",
    "poster_deploy_reset": "Poster Deployment Reset",
    "poster_maintenance": "Poster Maintenance",
    "poster_heal": "Poster Healing",
    "poster_pipeline": "Poster Pipeline",
    "poster_pipeline_batch": "Poster Pipeline (Batch)",
    "poster_pipeline_tv_batch": "TV Poster Pipeline (Batch)",
    "poster_rescan": "Poster Rescan",
    "system_metrics_purge": "Metrics Cleanup",
    "system_noop": "Health Check",
    "taste_enrich": "Taste Profile Enrichment",
    "taste_map": "Taste Map Generation",
    "taste_rebuild": "Taste Model Rebuild",
}


def humanize_job_type(job_type: str) -> str:
    """Return the operator-friendly label for a job type or operation."""
    if not job_type:
        return ""
    return JOB_LABELS.get(
        job_type,
        " ".join(word.capitalize() for word in job_type.split("_") if word),
    )
