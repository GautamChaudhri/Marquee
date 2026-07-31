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
    "poster_pipeline_group": "Poster Pipeline Group",
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


def poster_group_display_name(
    *, library: str, chunk_index: int, chunk_total: int | None, batch_mode: str
) -> str:
    """Name a poster group by what it does and which group it is.

    Shared by submission (which writes the durable snapshot) and the presenter (which
    re-derives it), so a stored name and a rendered one never drift apart. The member
    count is deliberately absent — the roster below the card already counts them.
    """
    prefix = "Get Film Posters" if library == "movies" else "Get Television Posters"
    if batch_mode == "all_at_once":
        return prefix
    position = f"Group {chunk_index + 1}"
    if chunk_total is not None:
        position = f"{position} of {chunk_total}"
    return f"{prefix} · {position}"


def poster_group_batch_context(*, parent_job_id: str | None) -> tuple[str, ...]:
    """Identify the submission a poster group came from.

    Batches have no human-readable number of their own, so the token is the tail of the
    parent job id — every group of one submission shares that parent.
    """
    if not parent_job_id:
        return ()
    return (f"Batch {parent_job_id[-4:].upper()}",)
