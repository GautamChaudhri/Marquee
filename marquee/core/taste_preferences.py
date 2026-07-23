"""Canonical taste evidence, retained exemplar assets, and derived readiness."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.jobs.artifact_service import (
    ArtifactError,
    physical_artifact_file,
    register_physical_artifact,
    verify_physical_artifact,
)
from marquee.models import (
    JobArtifact,
    MlActivePublication,
    PosterPreferenceEvent,
    TasteExemplar,
    TasteProfileRevision,
)

Namespace = Literal["global", "movies", "tv"]
Polarity = Literal["positive", "negative"]

_NAMESPACES = frozenset({"global", "movies", "tv"})
_ACTIONS = frozenset({"selection", "approval", "override", "rank", "hate", "undo", "revoke"})
_CONFIDENCE = frozenset({"explicit", "strong", "medium", "weak"})
_MAX_EVENT_BYTES = 64 * 1024
_MAX_EXPOSED = 100
_MAX_ACTIVE_NEGATIVES_PER_SUBJECT = 8


class TastePreferenceError(RuntimeError):
    """Canonical taste evidence violates its bounded or lineage contract."""


@dataclass(frozen=True, slots=True)
class ReadinessThresholds:
    required: int = 50
    encouraged: int = 75
    strong_target: int = 100

    def __post_init__(self) -> None:
        if not (1 <= self.required <= self.encouraged <= self.strong_target <= 10_000):
            raise ValueError("taste readiness thresholds are invalid")


@dataclass(frozen=True, slots=True)
class TasteReadiness:
    state: str
    active_positive_subjects: int
    active_negative_subjects: int
    pending_positive_subjects: int
    revision: str
    thresholds: ReadinessThresholds
    build_revision: str | None
    build_job_id: str | None
    profile_generations: dict[str, int]
    consumer_reloaded: bool
    next_action: str
    failure: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["thresholds"] = asdict(self.thresholds)
        return value


def _bounded_document(value: Any, *, label: str, max_bytes: int = _MAX_EVENT_BYTES) -> None:
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise TastePreferenceError(f"{label} is not finite JSON") from exc
    if len(encoded) > max_bytes:
        raise TastePreferenceError(f"{label} exceeds its storage bound")


def evidence_revision(exemplars: list[TasteExemplar]) -> str:
    """Hash the exact ordered active evidence identity without physical paths."""
    rows = sorted(
        (
            row.id,
            row.namespace,
            row.polarity,
            row.subject_kind,
            row.subject_reference,
            row.checksum or "",
            f"{row.evidence_weight:.8f}",
        )
        for row in exemplars
        if row.status == "active"
    )
    payload = json.dumps(rows, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(payload).hexdigest()


def profile_input_ids(exemplars: list[TasteExemplar], library: str) -> set[str]:
    """Resolve deterministic global-plus-namespace profile applicability."""
    if library not in {"movies", "tv"}:
        raise TastePreferenceError("profile library is invalid")
    return {
        row.id for row in exemplars if row.namespace in {"global", library}
    }


def profile_rebuild_due(
    current: set[str], prior: set[str], *, threshold: int, force: bool = False
) -> bool:
    """Use symmetric immutable-evidence change so additions and undo both coalesce."""
    if not 1 <= threshold <= 10_000:
        raise TastePreferenceError("profile rebuild coalescing threshold is invalid")
    return force or len(current ^ prior) >= threshold


async def append_preference_event(
    session: AsyncSession,
    *,
    idempotency_key: str,
    namespace: Namespace,
    subject_kind: str,
    subject_reference: str,
    subject_snapshot: dict[str, Any],
    action: str,
    exposed_candidates: list[dict[str, Any]],
    presentation_order: list[str],
    training_context: dict[str, Any],
    confidence: str,
    initiator: dict[str, Any],
    pipeline_run_id: str | None = None,
    candidate_artifact_id: int | None = None,
    supersedes_event_id: str | None = None,
) -> PosterPreferenceEvent:
    """Append one idempotent immutable event; callers own the transaction."""
    if namespace not in _NAMESPACES or action not in _ACTIONS or confidence not in _CONFIDENCE:
        raise TastePreferenceError("preference event vocabulary is invalid")
    if not idempotency_key or len(idempotency_key) > 160:
        raise TastePreferenceError("preference event idempotency key is invalid")
    if not subject_kind or not subject_reference or len(exposed_candidates) > _MAX_EXPOSED:
        raise TastePreferenceError("preference event subject or exposure is invalid")
    if len(presentation_order) != len(set(presentation_order)):
        raise TastePreferenceError("presentation order contains duplicate identities")
    exposed_ids = {
        candidate.get("candidate_id")
        for candidate in exposed_candidates
        if isinstance(candidate, dict) and isinstance(candidate.get("candidate_id"), str)
    }
    if not set(presentation_order).issubset(exposed_ids):
        raise TastePreferenceError("presentation order contains an unexposed candidate")
    document = {
        "subject_snapshot": subject_snapshot,
        "exposed_candidates": exposed_candidates,
        "presentation_order": presentation_order,
        "training_context": training_context,
        "initiator": initiator,
    }
    _bounded_document(document, label="preference event")
    existing = await session.scalar(
        select(PosterPreferenceEvent).where(
            PosterPreferenceEvent.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        return existing
    event = PosterPreferenceEvent(
        id=secrets.token_hex(16),
        version=1,
        idempotency_key=idempotency_key,
        namespace=namespace,
        subject_kind=subject_kind,
        subject_reference=subject_reference,
        subject_snapshot=subject_snapshot,
        pipeline_run_id=pipeline_run_id,
        candidate_artifact_id=candidate_artifact_id,
        action=action,
        exposed_candidates=exposed_candidates,
        presentation_order=presentation_order,
        training_context=training_context,
        confidence=confidence,
        initiator=initiator,
        supersedes_event_id=supersedes_event_id,
    )
    session.add(event)
    await session.flush((event,))
    return event


async def create_pending_exemplar(
    session: AsyncSession,
    *,
    event: PosterPreferenceEvent,
    polarity: Polarity,
    evidence_source: str,
    evidence_weight: float,
    deployment_job_id: str | None,
) -> TasteExemplar:
    """Create the only pending projection for an event; positive rows await deploy validation."""
    if event.action not in _ACTIONS or polarity not in {"positive", "negative"}:
        raise TastePreferenceError("exemplar evidence is invalid")
    if not 0 < evidence_weight <= 1 or len(evidence_source) > 32:
        raise TastePreferenceError("exemplar weight or source is invalid")
    if polarity == "positive" and deployment_job_id is None:
        raise TastePreferenceError("positive exemplar requires a canonical deployment job")
    existing = await session.scalar(
        select(TasteExemplar).where(TasteExemplar.preference_event_id == event.id)
    )
    if existing is not None:
        return existing
    row = TasteExemplar(
        id=secrets.token_hex(16),
        version=1,
        namespace=event.namespace,
        polarity=polarity,
        evidence_weight=evidence_weight,
        evidence_source=evidence_source,
        subject_kind=event.subject_kind,
        subject_reference=event.subject_reference,
        subject_snapshot=event.subject_snapshot,
        pipeline_run_id=event.pipeline_run_id,
        candidate_artifact_id=event.candidate_artifact_id,
        preference_event_id=event.id,
        deployment_job_id=deployment_job_id,
        initiator=event.initiator,
        status="pending_deploy",
    )
    session.add(row)
    await session.flush((row,))
    return row


async def pin_candidate_artifact(
    source: JobArtifact,
    *,
    deployment_job_id: str,
    deployment_attempt_id: int,
    deployment_fence_token: int,
) -> JobArtifact:
    """Copy selected bytes through the canonical artifact boundary into pinned retention."""
    if source.status != "available" or not source.checksum or source.storage_key is None:
        raise TastePreferenceError("candidate artifact is not available for promotion")
    await verify_physical_artifact(source)
    _boundary, classified = physical_artifact_file(source)
    pinned = await register_physical_artifact(
        job_id=deployment_job_id,
        attempt_id=deployment_attempt_id,
        fence_token=deployment_fence_token,
        source=classified,
        kind="taste_exemplar",
        name=f"taste-exemplar-{source.id}.jpg",
        content_type="image/jpeg",
        retention_class="pinned",
        metadata={"source_artifact_id": source.id, "source_checksum": source.checksum},
    )
    if pinned.checksum != source.checksum or pinned.expires_at is not None:
        raise ArtifactError("pinned exemplar promotion changed bytes or retention")
    return pinned


async def activate_exemplar(
    session: AsyncSession,
    *,
    exemplar_id: str,
    retained_artifact_id: int,
    deployment_result: dict[str, Any],
) -> TasteExemplar:
    """Promote only after successful canonical deploy and post-effect validation."""
    row = await session.scalar(
        select(TasteExemplar).where(TasteExemplar.id == exemplar_id).with_for_update()
    )
    if row is None:
        raise TastePreferenceError("pending exemplar is unavailable")
    if row.status == "active":
        return row
    if row.status != "pending_deploy":
        raise TastePreferenceError("only a pending exemplar can activate")
    if deployment_result.get("outcome") != "succeeded" or not deployment_result.get("validated"):
        raise TastePreferenceError("deployment did not complete post-effect validation")
    artifact = await session.get(JobArtifact, retained_artifact_id)
    if (
        artifact is None
        or artifact.kind != "taste_exemplar"
        or artifact.status != "available"
        or artifact.retention_class != "pinned"
        or artifact.expires_at is not None
        or not artifact.checksum
        or artifact.storage_key is None
        or artifact.job_id != row.deployment_job_id
    ):
        raise TastePreferenceError("retained exemplar artifact is invalid")
    if row.polarity == "negative":
        duplicate_negative = await session.scalar(
            select(TasteExemplar).where(
                TasteExemplar.id != row.id,
                TasteExemplar.status == "active",
                TasteExemplar.polarity == "negative",
                TasteExemplar.namespace == row.namespace,
                TasteExemplar.checksum == artifact.checksum,
            )
        )
        negative_count = len(
            list(
                await session.scalars(
                    select(TasteExemplar.id).where(
                        TasteExemplar.status == "active",
                        TasteExemplar.polarity == "negative",
                        TasteExemplar.namespace == row.namespace,
                        TasteExemplar.subject_kind == row.subject_kind,
                        TasteExemplar.subject_reference == row.subject_reference,
                    )
                )
            )
        )
        if duplicate_negative is not None or negative_count >= _MAX_ACTIVE_NEGATIVES_PER_SUBJECT:
            row.retained_artifact_id = artifact.id
            row.asset_key = artifact.storage_key
            row.checksum = artifact.checksum
            row.content_type = artifact.content_type
            row.deployment_result = deployment_result
            row.status = "invalid"
            row.reason = (
                "duplicate active negative content"
                if duplicate_negative is not None
                else "active negative subject cap reached"
            )
            await session.flush((row,))
            return row
    if row.polarity == "positive":
        duplicate_content = await session.scalar(
            select(TasteExemplar).where(
                TasteExemplar.id != row.id,
                TasteExemplar.status == "active",
                TasteExemplar.polarity == "positive",
                TasteExemplar.checksum == artifact.checksum,
            )
        )
        if duplicate_content is not None:
            row.retained_artifact_id = artifact.id
            row.asset_key = artifact.storage_key
            row.checksum = artifact.checksum
            row.content_type = artifact.content_type
            row.deployment_result = deployment_result
            row.status = "invalid"
            row.reason = f"duplicate active preference content: {duplicate_content.id}"
            await session.flush((row,))
            return row
        prior_subject = await session.scalar(
            select(TasteExemplar)
            .where(
                TasteExemplar.id != row.id,
                TasteExemplar.status == "active",
                TasteExemplar.polarity == "positive",
                TasteExemplar.namespace == row.namespace,
                TasteExemplar.subject_kind == row.subject_kind,
                TasteExemplar.subject_reference == row.subject_reference,
            )
            .with_for_update()
        )
        if prior_subject is not None:
            prior_subject.status = "revoked"
            prior_subject.reason = "superseded by a later explicit deployed selection"
            prior_subject.revoked_at = datetime.now(UTC)
            row.supersedes_exemplar_id = prior_subject.id
            source = await session.get(PosterPreferenceEvent, prior_subject.preference_event_id)
            if source is not None:
                source.revoked_event_id = row.preference_event_id
    row.retained_artifact_id = artifact.id
    row.asset_key = artifact.storage_key
    row.checksum = artifact.checksum
    row.content_type = artifact.content_type
    row.deployment_result = deployment_result
    row.status = "active"
    row.activated_at = datetime.now(UTC)
    await session.flush((row,))
    return row


async def revoke_exemplar(
    session: AsyncSession, *, exemplar_id: str, event: PosterPreferenceEvent, reason: str
) -> TasteExemplar:
    row = await session.scalar(
        select(TasteExemplar).where(TasteExemplar.id == exemplar_id).with_for_update()
    )
    if row is None:
        raise TastePreferenceError("exemplar is unavailable")
    if event.action not in {"undo", "revoke"} or event.supersedes_event_id != row.preference_event_id:
        raise TastePreferenceError("revocation event does not supersede the exemplar evidence")
    if row.status == "revoked":
        return row
    row.status = "revoked"
    row.reason = reason[:500]
    row.revoked_at = datetime.now(UTC)
    source = await session.get(PosterPreferenceEvent, row.preference_event_id)
    if source is not None:
        source.revoked_event_id = event.id
    await session.flush((row,))
    return row


async def snapshot_profile_revision(
    session: AsyncSession, *, namespaces: tuple[Namespace, ...] = ("global", "movies", "tv")
) -> TasteProfileRevision:
    """Freeze all active global and namespace evidence into one immutable revision."""
    if not namespaces or not set(namespaces).issubset(_NAMESPACES):
        raise TastePreferenceError("profile revision namespaces are invalid")
    rows = list(
        (
            await session.scalars(
                select(TasteExemplar)
                .where(
                    TasteExemplar.status == "active",
                    TasteExemplar.namespace.in_(namespaces),
                )
                .order_by(TasteExemplar.id)
                .with_for_update()
            )
        ).all()
    )
    digest = evidence_revision(rows)
    existing = await session.get(TasteProfileRevision, digest)
    if existing is not None:
        return existing
    positives = {
        (row.subject_kind, row.subject_reference)
        for row in rows
        if row.namespace == "global" and row.polarity == "positive"
    }
    revision = TasteProfileRevision(
        digest=digest,
        exemplar_ids=[row.id for row in rows],
        exemplar_checksums=[row.checksum or "" for row in rows],
        positive_subjects=len(positives),
        state="eligible",
        consumer_reload={},
    )
    session.add(revision)
    for row in rows:
        row.revision_digest = digest
    await session.flush()
    return revision


async def schedule_initial_profile_build(
    session: AsyncSession, *, initiator_identifier: str
) -> tuple[Any, ...]:
    """Compatibility wrapper for the first revision-keyed movie/TV build."""
    return await schedule_profile_builds(
        session,
        namespaces=("movies", "tv"),
        initiator_identifier=initiator_identifier,
        initial_only=True,
    )


async def schedule_profile_builds(
    session: AsyncSession,
    *,
    namespaces: tuple[Literal["movies", "tv"], ...],
    initiator_identifier: str,
    force: bool = False,
    coalesce_threshold: int = 10,
    initial_only: bool = False,
) -> tuple[Any, ...]:
    """Coalesce immutable global-plus-namespace revisions into canonical builds."""
    if not namespaces or not set(namespaces).issubset({"movies", "tv"}):
        raise TastePreferenceError("profile rebuild namespaces are invalid")
    if not 1 <= coalesce_threshold <= 10_000:
        raise TastePreferenceError("profile rebuild coalescing threshold is invalid")
    readiness = await derive_readiness(session)
    if readiness.active_positive_subjects < readiness.thresholds.required:
        return ()
    existing_publication = await session.scalar(
        select(MlActivePublication.family).where(
            MlActivePublication.family.in_(("taste_profile:movies", "taste_profile:tv"))
        )
    )
    revision = await snapshot_profile_revision(
        session,
        namespaces=("global",)
        if initial_only or existing_publication is None
        else ("global", "movies", "tv"),
    )
    from marquee.core.jobs.contracts import TriggerKind  # noqa: PLC0415
    from marquee.core.jobs.submission import (  # noqa: PLC0415
        Initiator,
        SubjectLocator,
        submit_job,
    )

    submissions = []
    for library in dict.fromkeys(namespaces):
        publication = await session.get(MlActivePublication, f"taste_profile:{library}")
        if initial_only and publication is not None:
            continue
        current_rows = list(
            await session.scalars(
                select(TasteExemplar).where(TasteExemplar.id.in_(revision.exemplar_ids))
            )
        )
        relevant = profile_input_ids(current_rows, library)
        prior: set[str] = set()
        if publication is not None:
            artifact = await session.get(JobArtifact, publication.artifact_id)
            metadata = artifact.artifact_metadata if artifact is not None else None
            prior_digest = metadata.get("revision") if isinstance(metadata, dict) else None
            prior_revision = (
                await session.get(TasteProfileRevision, prior_digest)
                if isinstance(prior_digest, str)
                else None
            )
            if prior_revision is not None:
                prior_rows = list(
                    await session.scalars(
                        select(TasteExemplar).where(
                            TasteExemplar.id.in_(prior_revision.exemplar_ids)
                        )
                    )
                )
                prior = profile_input_ids(prior_rows, library)
        if publication is not None and not profile_rebuild_due(
            relevant, prior, threshold=coalesce_threshold, force=force
        ):
            continue
        submissions.append(
            await submit_job(
                session,
                job_type="taste_rebuild",
                request={
                    "source": "canonical_revision",
                    "library": library,
                    "revision": revision.digest,
                },
                subject=SubjectLocator(
                    kind="model_profile_training",
                    reference=f"taste_profile:{library}:{revision.digest[:12]}",
                ),
                trigger=TriggerKind.MANUAL,
                initiator=Initiator(kind="system", identifier=initiator_identifier),
                idempotency_key=f"taste_rebuild:{library}:{revision.digest}",
                priority=90,
            )
        )
    if not submissions:
        return ()
    revision.state = "building"
    revision.build_job_id = submissions[0].job_id
    revision.failure = None
    await session.flush((revision,))
    return tuple(submissions)


async def derive_readiness(
    session: AsyncSession, *, thresholds: ReadinessThresholds | None = None
) -> TasteReadiness:
    """Reconstruct onboarding state from canonical evidence and publication/reload lineage."""
    thresholds = thresholds or ReadinessThresholds()
    rows = list(
        (
            await session.scalars(
                select(TasteExemplar).where(TasteExemplar.namespace == "global")
            )
        ).all()
    )
    active = [row for row in rows if row.status == "active"]
    positives = {
        (row.subject_kind, row.subject_reference)
        for row in active
        if row.polarity == "positive"
    }
    negatives = {
        (row.subject_kind, row.subject_reference)
        for row in active
        if row.polarity == "negative"
    }
    pending = {
        (row.subject_kind, row.subject_reference)
        for row in rows
        if row.status == "pending_deploy" and row.polarity == "positive"
    }
    revision = evidence_revision(active)
    build = await session.get(TasteProfileRevision, revision)
    publications = list(
        (
            await session.scalars(
                select(MlActivePublication).where(
                    or_(
                        MlActivePublication.family == "taste_profile:movies",
                        MlActivePublication.family == "taste_profile:tv",
                    )
                )
            )
        ).all()
    )
    generations = {row.family.rpartition(":")[2]: row.generation for row in publications}
    reloads = build.consumer_reload if build is not None else {}
    consumer_reloaded = bool(
        build is not None
        and build.state == "personalized"
        and build.movie_checksum
        and build.tv_checksum
        and reloads.get("movies") == build.movie_checksum
        and reloads.get("tv") == build.tv_checksum
    )
    count = len(positives)
    if consumer_reloaded:
        state, next_action = "personalized", "continue refining taste"
    elif build is not None and build.state == "building":
        state, next_action = "building", "wait for profile validation and reload"
    elif build is not None and build.state == "failed":
        state, next_action = "degraded", "retry the failed profile build"
    elif count >= thresholds.required:
        state, next_action = "eligible", "build movie and TV taste profiles"
    else:
        state, next_action = "collecting", "choose and deploy another poster"
    return TasteReadiness(
        state=state,
        active_positive_subjects=count,
        active_negative_subjects=len(negatives),
        pending_positive_subjects=len(pending),
        revision=revision,
        thresholds=thresholds,
        build_revision=build.digest if build is not None else None,
        build_job_id=build.build_job_id if build is not None else None,
        profile_generations=generations,
        consumer_reloaded=consumer_reloaded,
        next_action=next_action,
        failure=build.failure if build is not None else None,
    )
