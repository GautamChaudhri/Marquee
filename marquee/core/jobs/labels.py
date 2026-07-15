"""Human-friendly labels for durable job and media-job types."""

from __future__ import annotations

JOB_LABELS: dict[str, str] = {
    "audio_remove": "Audio Track Removal",
    "subtitle_policy_batch": "Subtitle Policy Batch",
    "audio_subs_deep_scan": "Audio & Subtitle Deep Scan",
    "audio_reorder": "Audio Stream Reorder",
    "backup_create": "Backup Creation",
    "dovi_analyze": "Dolby Vision Analysis",
    "dovi_analyze_batch": "Dolby Vision Analysis (Batch)",
    "dovi_convert": "Dolby Vision Conversion",
    "job_retention_purge": "Job Retention Cleanup",
    "learned_head_train": "Learned Model Training",
    "letterbox_apply": "Letterbox Crop Apply",
    "letterbox_apply_batch": "Letterbox Crop Apply (Batch)",
    "letterbox_apply_tv_scope": "TV Letterbox Crop Apply",
    "letterbox_detect": "Letterbox Detection",
    "letterbox_detect_batch": "Letterbox Detection (Batch)",
    "letterbox_detect_episode": "Episode Letterbox Detection",
    "letterbox_detect_tv_batch": "TV Letterbox Detection (Batch)",
    "letterbox_detect_tv_scope": "TV Letterbox Detection",
    "letterbox_heal": "Letterbox Healing",
    "letterbox_reencode": "Letterbox Re-encode",
    "letterbox_reencode_tv_batch": "TV Letterbox Re-encode (Batch)",
    "letterbox_remove": "Letterbox Crop Remove",
    "letterbox_revert_tv_scope": "TV Letterbox Revert",
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
    "radarr_upgrade": "Radarr Upgrade",
    "subtitle_embed": "Subtitle Embedding",
    "subtitle_extract": "Subtitle Extraction",
    "subtitle_generate": "Subtitle Generation",
    "subtitle_generate_batch": "Subtitle Generation (Batch)",
    "subtitle_metadata": "Subtitle Metadata Edit",
    "subtitle_policy": "Subtitle Policy Apply",
    "subtitle_remove": "Subtitle Removal",
    "subtitle_restore": "Subtitle Restore",
    "subtitle_scan": "Subtitle Scan",
    "subtitle_scan_all": "Subtitle Scan (All)",
    "system_metrics_purge": "Metrics Cleanup",
    "system_noop": "Health Check",
    "taste_map": "Taste Map Generation",
    "taste_rebuild": "Taste Model Rebuild",
    "track_remove": "Track Removal",
}


def humanize_job_type(job_type: str) -> str:
    """Return the operator-friendly label for a job type or operation."""
    if not job_type:
        return ""
    return JOB_LABELS.get(
        job_type,
        " ".join(word.capitalize() for word in job_type.split("_") if word),
    )
