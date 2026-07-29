"""Canonical immutable archive reader for PipelineRun product projections."""

from __future__ import annotations

import asyncio
import json

from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.jobs.artifact_service import verify_physical_artifact
from marquee.models import JobArtifact, PipelineRun

_MAX_ARCHIVE_BYTES = 1024 * 1024


async def load_pipeline_archive(session: AsyncSession, run: PipelineRun) -> dict | None:
    """Load and verify the archive artifact linked by the canonical run projection."""
    if run.archive_artifact_id is None:
        return None
    artifact = await session.get(JobArtifact, run.archive_artifact_id)
    if (
        artifact is None
        or artifact.status != "available"
        or artifact.kind != "command_report"
        or artifact.job_id != run.job_id
        or artifact.size_bytes is None
        or artifact.size_bytes > _MAX_ARCHIVE_BYTES
        or not isinstance(artifact.artifact_metadata, dict)
        or artifact.artifact_metadata.get("family") != "poster_pipeline"
    ):
        return None
    _boundary, classified = await verify_physical_artifact(artifact)
    path = classified.root.resolved() / classified.key.value
    try:
        document = json.loads(await asyncio.to_thread(path.read_text))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    if artifact.artifact_metadata.get("group_fallback") is not True:
        return document
    members = document.get("members")
    if not isinstance(members, list):
        return None
    for member in members:
        if isinstance(member, dict) and member.get("subject_key") == run.subject_key:
            return member
    return None
