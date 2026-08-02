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


def poster_group_display_name(*, library: str, member_count: int) -> str:
    """Name a poster group by its action and immutable subject count."""
    prefix = "Get Film Posters" if library == "movies" else "Get Television Posters"
    noun = "Subject" if member_count == 1 else "Subjects"
    return f"{prefix} · {member_count} {noun}"


def poster_group_disclosure_label(batch_mode: str) -> str:
    """Name what the roster button opens for this group's batch shape.

    A unified run puts every selected subject in one execution card, so the
    roster below it is the run. A chunked run splits them across several, and
    each card owns only its own group.
    """
    return "Posters in this run" if batch_mode == "all_at_once" else "Posters in this group"


def poster_group_batch_context(
    *,
    parent_job_id: str | None,
    chunk_index: int,
    chunk_total: int | None,
    batch_mode: str,
) -> tuple[str, ...]:
    """Identify the batch and chunk position without repeating either in the title."""
    if batch_mode == "all_at_once":
        # One execution card owns the selected poster set, so do not expose the
        # structural coordinator's batch identifier or a meaningless group number.
        return ("Unified Run",)
    context: list[str] = []
    if parent_job_id:
        context.append(f"Batch {parent_job_id[-4:].upper()}")
    if batch_mode == "chunked":
        position = f"Group {chunk_index + 1}"
        if chunk_total is not None:
            position = f"{position} of {chunk_total}"
        context.append(position)
    return tuple(context)
