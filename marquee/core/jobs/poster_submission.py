"""Server-owned poster selection snapshots and canonical leaf submission."""

from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.filesystem import FilesystemBoundary, RootSpec
from marquee.core.jobs.contracts import TriggerKind
from marquee.core.jobs.mutation_documents import PosterCandidateSelectionV1
from marquee.core.jobs.publication import file_signature
from marquee.core.jobs.submission import (
    Initiator,
    SubjectLocator,
    SubmissionResult,
    submit_job,
)
from marquee.core.path_utils import safe_translate_and_validate
from marquee.core.poster_subjects import PosterSubject
from marquee.core.runtime_settings import effective_settings as settings
from marquee.models import PipelineRun


class PosterSelectionError(ValueError):
    pass


def poster_child_idempotency_key(root_key: str, subject_key: str) -> str:
    """Derive a bounded, path-free leaf key from an explicit bulk request key."""
    digest = hashlib.sha256(f"{root_key}:{subject_key}".encode()).hexdigest()
    return f"poster_deploy:{digest}"


def pipeline_candidate_selection(
    run: PipelineRun,
    candidate: dict,
    *,
    selection_facts: dict | None = None,
    allow_provider_original: bool = False,
) -> PosterCandidateSelectionV1:
    """Name the bytes to deploy for a candidate the user picked out of a run.

    A run only re-fetches its top-ranked candidates at full resolution — every
    other candidate, ranked or rejected, is archived at w500. Publishing that
    would put a ~500px poster in the media folder, so review callers pass
    ``allow_provider_original`` and anything without an original-size download is
    deployed by re-fetching the provider original instead.

    Onboarding must *not* pass it: it binds a taste exemplar to the stored
    artifact and later proves the deployed bytes match that artifact's checksum,
    which re-fetched bytes would not satisfy.
    """
    reference = candidate.get("orig_filename")
    if not isinstance(reference, str) or not reference:
        raise PosterSelectionError("candidate identity is missing")
    if allow_provider_original and candidate.get("original_download") is not True:
        return PosterCandidateSelectionV1(
            source="provider_original",
            run_id=run.run_id,
            candidate_reference=reference,
            selection_facts=selection_facts or {},
        )
    artifact_id = candidate.get("artifact_id")
    artifact_key = candidate.get("artifact_storage_key")
    artifact_checksum = candidate.get("artifact_checksum")
    if (
        isinstance(artifact_id, int)
        and artifact_id > 0
        and isinstance(artifact_key, str)
        and artifact_key
        and isinstance(artifact_checksum, str)
        and len(artifact_checksum) == 64
    ):
        return PosterCandidateSelectionV1(
            source="pipeline_run",
            storage_key=artifact_key,
            artifact_id=artifact_id,
            run_id=run.run_id,
            candidate_reference=reference,
            expected_checksum=artifact_checksum,
            selection_facts=selection_facts or {},
        )
    raise PosterSelectionError("selected candidate is missing canonical artifact identity")


def subject_artwork_selection(
    subject: PosterSubject, *, selection_facts: dict | None = None
) -> PosterCandidateSelectionV1:
    if not subject.folder_raw:
        raise PosterSelectionError("recorded subject folder is unavailable")
    try:
        folder = safe_translate_and_validate(subject.folder_raw, source=subject.path_source)
    except ValueError as exc:
        raise PosterSelectionError("recorded subject folder is unavailable") from exc
    boundary = FilesystemBoundary(
        {
            "data": RootSpec(
                name="data",
                path=Path(settings.DATA_DIR),
                purpose="recorded poster source snapshot",
                allow_symlinks=False,
            ),
            "subject": RootSpec(
                name="subject",
                path=folder,
                purpose="deployed poster source snapshot",
                allow_symlinks=False,
            ),
        }
    )
    choices = [subject.backup_file()]
    cache = subject.cache_paths()
    if cache is not None:
        choices.append(cache[0])
    if subject.entity.poster_path:
        choices.append(Path(subject.entity.poster_path))
    source = None
    for choice in choices:
        for root in ("data", "subject"):
            try:
                source = boundary.classify(choice, roots=(root,), require_file=True)
                break
            except ValueError:
                continue
        if source is not None:
            break
    if source is None:
        raise PosterSelectionError("recorded subject artwork bytes are unavailable")
    signature = file_signature(boundary, source)
    return PosterCandidateSelectionV1(
        source="subject_artwork",
        storage_key=f"subject-artwork/{subject.media_type}/{subject.id}",
        source_kind=subject.media_type,
        source_id=subject.id,
        expected_checksum=signature.sha256,
        selection_facts=selection_facts or {},
    )


async def submit_poster_leaf(
    session: AsyncSession,
    *,
    job_type: str,
    target_kind: str,
    target_id: int,
    request: dict[str, object],
    idempotency_key: str,
    initiator: str,
) -> SubmissionResult:
    return await submit_job(
        session,
        job_type=job_type,
        request=request,
        subject=SubjectLocator(kind=target_kind, reference=str(target_id)),
        trigger=TriggerKind.MANUAL,
        initiator=Initiator(kind="api", identifier=initiator),
        idempotency_key=idempotency_key,
    )
