"""Canonical taste evidence, retained exemplar assets, and derived readiness."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.jobs.artifact_service import (
    ArtifactError,
    physical_artifact_file,
    register_physical_artifact,
    verify_physical_artifact,
)
from marquee.models import (
    Job,
    JobArtifact,
    MlActivePublication,
    PosterPreferenceEvent,
    TasteExemplar,
    TasteProfileBuild,
    TasteProfileCoordinator,
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
    libraries: dict[str, dict[str, Any]]
    initial_profiles_ready: bool
    personalized_scoring_available: bool
    rebuild_due: bool
    residual_dormant: bool

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


async def create_active_negative_exemplar(
    session: AsyncSession,
    *,
    event: PosterPreferenceEvent,
    retained_artifact: JobArtifact,
    evidence_source: str,
    evidence_weight: float,
) -> TasteExemplar:
    """Project explicit dislike evidence without inventing a deployment outcome.

    A negative exemplar retains the reviewed candidate for training, but it is not a
    deployment assertion.  The pinning job may be the analysis job that produced the
    canonical evidence; ``deployment_job_id`` and ``deployment_result`` deliberately
    remain null.
    """
    if event.action != "hate" or event.candidate_artifact_id is None:
        raise TastePreferenceError("negative exemplar requires canonical hate evidence")
    if not 0 < evidence_weight <= 1 or len(evidence_source) > 32:
        raise TastePreferenceError("exemplar weight or source is invalid")
    if (
        retained_artifact.kind != "taste_exemplar"
        or retained_artifact.status != "available"
        or retained_artifact.retention_class != "pinned"
        or retained_artifact.expires_at is not None
        or not retained_artifact.checksum
        or retained_artifact.storage_key is None
    ):
        raise TastePreferenceError("retained negative exemplar artifact is invalid")
    existing = await session.scalar(
        select(TasteExemplar).where(TasteExemplar.preference_event_id == event.id)
    )
    if existing is not None:
        return existing

    row = TasteExemplar(
        id=secrets.token_hex(16),
        version=1,
        namespace=event.namespace,
        polarity="negative",
        evidence_weight=evidence_weight,
        evidence_source=evidence_source,
        subject_kind=event.subject_kind,
        subject_reference=event.subject_reference,
        subject_snapshot=event.subject_snapshot,
        pipeline_run_id=event.pipeline_run_id,
        candidate_artifact_id=event.candidate_artifact_id,
        retained_artifact_id=retained_artifact.id,
        preference_event_id=event.id,
        deployment_job_id=None,
        deployment_result=None,
        initiator=event.initiator,
        asset_key=retained_artifact.storage_key,
        checksum=retained_artifact.checksum,
        content_type=retained_artifact.content_type,
        status="active",
        activated_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush((row,))

    duplicate_negative = await session.scalar(
        select(TasteExemplar).where(
            TasteExemplar.id != row.id,
            TasteExemplar.status == "active",
            TasteExemplar.polarity == "negative",
            TasteExemplar.namespace == row.namespace,
            TasteExemplar.checksum == row.checksum,
        )
    )
    negative_count = len(
        list(
            await session.scalars(
                select(TasteExemplar.id).where(
                    TasteExemplar.id != row.id,
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
        row.status = "invalid"
        row.reason = (
            "duplicate active negative content"
            if duplicate_negative is not None
            else "active negative subject cap reached"
        )
        await session.flush((row,))
    return row


async def pin_candidate_artifact(
    source: JobArtifact,
    *,
    deployment_job_id: str,
    deployment_attempt_id: int,
    deployment_fence_token: int,
    session: AsyncSession | None = None,
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
        session=session,
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
    """Schedule the initial movie/TV pair through the durable coordinators."""
    return await schedule_profile_builds(
        session,
        namespaces=("movies", "tv"),
        initiator_identifier=initiator_identifier,
        initial_only=True,
    )


_PROFILE_LIBRARIES = ("movies", "tv")
_INFLIGHT_PROFILE_BUILD_STATES = frozenset({"queued", "running"})
_TERMINAL_PROFILE_BUILD_STATES = frozenset(
    {"succeeded", "no_change", "superseded", "failed", "cancelled"}
)


async def _lock_profile_coordinator(
    session: AsyncSession, library: Literal["movies", "tv"]
) -> TasteProfileCoordinator:
    """Serialize one library at the database boundary, not in process memory."""
    if session.get_bind().dialect.name != "postgresql":
        raise TastePreferenceError("taste profile coordination requires PostgreSQL transaction locks")
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": f"marquee:taste-profile:{library}"},
    )
    coordinator = await session.scalar(
        select(TasteProfileCoordinator)
        .where(TasteProfileCoordinator.library == library)
        .with_for_update()
    )
    if coordinator is None:
        coordinator = TasteProfileCoordinator(library=library)
        session.add(coordinator)
        await session.flush((coordinator,))
    return coordinator


async def _locked_profile_build(
    session: AsyncSession, build_id: str | None
) -> TasteProfileBuild | None:
    if build_id is None:
        return None
    return await session.scalar(
        select(TasteProfileBuild).where(TasteProfileBuild.id == build_id).with_for_update()
    )


def _terminal_state_for_job(job: Job) -> Literal["failed", "cancelled", "superseded", "succeeded"]:
    if job.outcome == "cancelled":
        return "cancelled"
    if job.outcome == "superseded":
        return "superseded"
    if job.outcome in {"succeeded", "no_change"}:
        return "succeeded"
    return "failed"


async def _active_profile_details(
    session: AsyncSession, library: Literal["movies", "tv"]
) -> tuple[MlActivePublication | None, str | None]:
    publication = await session.get(MlActivePublication, f"taste_profile:{library}")
    if publication is None:
        return None, None
    artifact = await session.get(JobArtifact, publication.artifact_id)
    metadata = artifact.artifact_metadata if artifact is not None else None
    revision = metadata.get("revision") if isinstance(metadata, dict) else None
    return publication, revision if isinstance(revision, str) else None


async def _revision_is_due(
    session: AsyncSession,
    *,
    library: Literal["movies", "tv"],
    revision: TasteProfileRevision,
    force: bool,
    coalesce_threshold: int,
) -> bool:
    publication, active_revision_digest = await _active_profile_details(session, library)
    if publication is None:
        return True
    if active_revision_digest == revision.digest:
        return force
    active_revision = (
        await session.get(TasteProfileRevision, active_revision_digest)
        if active_revision_digest is not None
        else None
    )
    if active_revision is None:
        return True
    return profile_rebuild_due(
        set(revision.exemplar_ids),
        set(active_revision.exemplar_ids),
        threshold=coalesce_threshold,
        force=force,
    )


async def _latest_profile_build(
    session: AsyncSession,
    *,
    library: Literal["movies", "tv"],
    revision_digest: str,
    expected_generation: int,
) -> TasteProfileBuild | None:
    return await session.scalar(
        select(TasteProfileBuild)
        .where(
            TasteProfileBuild.library == library,
            TasteProfileBuild.revision_digest == revision_digest,
            TasteProfileBuild.expected_generation == expected_generation,
        )
        .order_by(TasteProfileBuild.created_at.desc(), TasteProfileBuild.id.desc())
        .limit(1)
    )


async def _submit_profile_build(
    session: AsyncSession,
    *,
    coordinator: TasteProfileCoordinator,
    library: Literal["movies", "tv"],
    revision: TasteProfileRevision,
    expected_generation: int,
    initiator_identifier: str,
) -> Any | None:
    """Submit one exact-generation build after the database lock has selected it."""
    from marquee.core.jobs.contracts import TriggerKind  # noqa: PLC0415
    from marquee.core.jobs.submission import (  # noqa: PLC0415
        Initiator,
        SubjectLocator,
        submit_job,
    )

    existing = await _latest_profile_build(
        session,
        library=library,
        revision_digest=revision.digest,
        expected_generation=expected_generation,
    )
    if existing is not None:
        if existing.state in _INFLIGHT_PROFILE_BUILD_STATES:
            coordinator.inflight_build_id = existing.id
        return None

    build_id = secrets.token_hex(16)
    submission = await submit_job(
        session,
        job_type="taste_rebuild",
        request={
            "source": "canonical_revision",
            "library": library,
            "revision": revision.digest,
            "profile_build_id": build_id,
            "expected_generation": expected_generation,
        },
        subject=SubjectLocator(
            kind="model_profile_training",
            reference=f"taste_profile:{library}",
        ),
        trigger=TriggerKind.MANUAL,
        initiator=Initiator(kind="system", identifier=initiator_identifier),
        idempotency_key=f"taste_rebuild:{library}:{revision.digest}:g{expected_generation}",
        priority=90,
    )
    job = await session.get(Job, submission.job_id)
    if job is None:
        raise TastePreferenceError("submitted profile build disappeared")
    request = job.request if isinstance(job.request, dict) else {}
    compatible_reuse = (
        request.get("source") == "canonical_revision"
        and request.get("library") == library
        and request.get("revision") == revision.digest
        and request.get("profile_build_id") == build_id
        and request.get("expected_generation") == expected_generation
    )
    if job.phase == "terminal" or not compatible_reuse:
        # A terminal or foreign idempotency reuse is evidence only. It may never make the
        # coordinator look building again, because a retry successor is the only recovery path.
        return None
    build = TasteProfileBuild(
        id=build_id,
        revision_digest=revision.digest,
        library=library,
        expected_generation=expected_generation,
        job_id=job.id,
        state="queued",
    )
    session.add(build)
    await session.flush((build,))
    coordinator.inflight_build_id = build.id
    coordinator.desired_generation = expected_generation
    return submission


async def _reconcile_locked_profile_coordinator(
    session: AsyncSession,
    *,
    coordinator: TasteProfileCoordinator,
    library: Literal["movies", "tv"],
    initiator_identifier: str,
    coalesce_threshold: int,
    force: bool = False,
    allow_terminal_successor: bool = False,
) -> Any | None:
    """Create at most one bounded successor for the coordinator's desired revision."""
    if coordinator.desired_revision_digest is None:
        return None
    revision = await session.get(TasteProfileRevision, coordinator.desired_revision_digest)
    if revision is None:
        raise TastePreferenceError("coordinator desired revision is unavailable")
    inflight = await _locked_profile_build(session, coordinator.inflight_build_id)
    if inflight is not None and inflight.state in _INFLIGHT_PROFILE_BUILD_STATES:
        job = await session.get(Job, inflight.job_id)
        if job is not None and job.phase != "terminal":
            return None
        inflight.state = "failed"
        inflight.failure = {"reason": "terminal_job_without_profile_completion"}
        inflight.completed_at = datetime.now(UTC)
        coordinator.inflight_build_id = None
    elif inflight is not None:
        coordinator.inflight_build_id = None

    if not await _revision_is_due(
        session,
        library=library,
        revision=revision,
        force=force,
        coalesce_threshold=coalesce_threshold,
    ):
        return None

    publication, _active_revision = await _active_profile_details(session, library)
    expected_generation = publication.generation if publication is not None else 0
    coordinator.desired_generation = expected_generation
    prior = await _latest_profile_build(
        session,
        library=library,
        revision_digest=revision.digest,
        expected_generation=expected_generation,
    )
    if prior is not None:
        if prior.state in _INFLIGHT_PROFILE_BUILD_STATES:
            coordinator.inflight_build_id = prior.id
            return None
        if prior.state in {"failed", "cancelled"} and not allow_terminal_successor:
            return None
        if prior.state == "superseded" and prior.expected_generation == expected_generation:
            # A new CAS generation is the only safe automatic successor. Repeating the same
            # generation would be an unbounded loop with no observable progress.
            return None
        if prior.state in {"succeeded", "no_change"}:
            return None
    return await _submit_profile_build(
        session,
        coordinator=coordinator,
        library=library,
        revision=revision,
        expected_generation=expected_generation,
        initiator_identifier=initiator_identifier,
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
    """Freeze desired evidence and serialize movie/TV publication independently."""
    if not namespaces or not set(namespaces).issubset({"movies", "tv"}):
        raise TastePreferenceError("profile rebuild namespaces are invalid")
    if not 1 <= coalesce_threshold <= 10_000:
        raise TastePreferenceError("profile rebuild coalescing threshold is invalid")
    readiness = await derive_readiness(session)
    if readiness.active_positive_subjects < readiness.thresholds.required:
        return ()
    submissions = []
    for library in dict.fromkeys(namespaces):
        coordinator = await _lock_profile_coordinator(session, library)
        publication = await session.get(MlActivePublication, f"taste_profile:{library}")
        if initial_only and publication is not None:
            continue
        revision = await snapshot_profile_revision(session, namespaces=("global", library))
        coordinator.desired_revision_digest = revision.digest
        coordinator.desired_generation = publication.generation if publication is not None else 0
        submission = await _reconcile_locked_profile_coordinator(
            session,
            coordinator=coordinator,
            library=library,
            initiator_identifier=initiator_identifier,
            coalesce_threshold=coalesce_threshold,
            force=force,
        )
        if submission is not None:
            submissions.append(submission)
    await session.flush()
    return tuple(submissions)


async def mark_profile_build_running(
    session: AsyncSession, *, build_id: str, job_id: str
) -> None:
    """Mark a queued coordinated build running; duplicate delivery is idempotent."""
    build = await session.get(TasteProfileBuild, build_id)
    if build is None or build.job_id != job_id:
        raise TastePreferenceError("profile build lineage is unavailable")
    coordinator = await _lock_profile_coordinator(session, build.library)
    build = await _locked_profile_build(session, build.id)
    if build is None:
        raise TastePreferenceError("profile build lineage is unavailable")
    if coordinator.inflight_build_id != build.id:
        raise TastePreferenceError("profile build is no longer coordinator in-flight work")
    if build.state == "queued":
        build.state = "running"
    elif build.state != "running":
        raise TastePreferenceError("terminal profile build cannot execute again")
    await session.flush((build, coordinator))


async def record_profile_build_terminal(
    session: AsyncSession,
    *,
    build_id: str,
    job_id: str,
    state: Literal["succeeded", "superseded", "failed", "cancelled", "no_change"],
    result_generation: int | None = None,
    result_checksum: str | None = None,
    consumer_reload_checksum: str | None = None,
    failure: dict[str, Any] | None = None,
    initiator_identifier: str = "taste-profile-handler",
) -> Any | None:
    """Persist one terminal outcome and reconcile at most one newer desired revision."""
    build = await session.get(TasteProfileBuild, build_id)
    if build is None or build.job_id != job_id:
        raise TastePreferenceError("profile build lineage is unavailable")
    coordinator = await _lock_profile_coordinator(session, build.library)
    build = await _locked_profile_build(session, build.id)
    if build is None:
        raise TastePreferenceError("profile build lineage is unavailable")
    if build.state in _TERMINAL_PROFILE_BUILD_STATES:
        return None
    build.state = state
    build.result_generation = result_generation
    build.result_checksum = result_checksum
    build.consumer_reload_checksum = consumer_reload_checksum
    build.failure = failure
    build.completed_at = datetime.now(UTC)
    if coordinator.inflight_build_id == build.id:
        coordinator.inflight_build_id = None
    successor = None
    if state == "superseded" or coordinator.desired_revision_digest != build.revision_digest:
        successor = await _reconcile_locked_profile_coordinator(
            session,
            coordinator=coordinator,
            library=build.library,
            initiator_identifier=initiator_identifier,
            coalesce_threshold=1,
            force=state == "superseded",
            allow_terminal_successor=state == "superseded",
        )
    await session.flush((build, coordinator))
    return successor


async def assert_profile_build_retryable(session: AsyncSession, *, job_id: str) -> TasteProfileBuild | None:
    """Validate that generic job retry will create a safe canonical build successor."""
    build = await session.scalar(
        select(TasteProfileBuild).where(TasteProfileBuild.job_id == job_id).with_for_update()
    )
    if build is None:
        return None
    coordinator = await _lock_profile_coordinator(session, build.library)
    if build.state not in {"failed", "cancelled"}:
        raise TastePreferenceError("only terminal failed or cancelled profile builds retry")
    if coordinator.inflight_build_id is not None:
        raise TastePreferenceError("profile coordinator already has in-flight work")
    if coordinator.desired_revision_digest != build.revision_digest:
        raise TastePreferenceError("profile retry is stale against newer desired evidence")
    publication = await session.get(MlActivePublication, f"taste_profile:{build.library}")
    active_generation = publication.generation if publication is not None else 0
    if active_generation != build.expected_generation:
        raise TastePreferenceError("profile retry is stale against the active publication generation")
    return build


async def record_profile_build_retry_successor(
    session: AsyncSession,
    *,
    original_job_id: str,
    successor_job_id: str,
    successor_build_id: str,
) -> TasteProfileBuild | None:
    """Attach the generic canonical retry successor to profile lineage after it exists."""
    original = await session.scalar(
        select(TasteProfileBuild)
        .where(TasteProfileBuild.job_id == original_job_id)
        .with_for_update()
    )
    if original is None:
        return None
    coordinator = await _lock_profile_coordinator(session, original.library)
    if original.state not in {"failed", "cancelled"}:
        raise TastePreferenceError("only terminal failed or cancelled profile builds retry")
    if coordinator.inflight_build_id is not None:
        raise TastePreferenceError("profile coordinator already has in-flight work")
    if coordinator.desired_revision_digest != original.revision_digest:
        raise TastePreferenceError("profile retry is stale against newer desired evidence")
    publication = await session.get(MlActivePublication, f"taste_profile:{original.library}")
    active_generation = publication.generation if publication is not None else 0
    if active_generation != original.expected_generation:
        raise TastePreferenceError("profile retry is stale against the active publication generation")
    successor = TasteProfileBuild(
        id=successor_build_id,
        revision_digest=original.revision_digest,
        library=original.library,
        expected_generation=original.expected_generation,
        job_id=successor_job_id,
        state="queued",
        supersedes_build_id=original.id,
    )
    session.add(successor)
    await session.flush((successor,))
    coordinator.inflight_build_id = successor.id
    coordinator.desired_generation = successor.expected_generation
    await session.flush((coordinator,))
    return successor


async def derive_readiness(
    session: AsyncSession, *, thresholds: ReadinessThresholds | None = None
) -> TasteReadiness:
    """Report current evidence, active publications, and per-library build lineage separately."""
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
    publication_by_library = {row.family.rpartition(":")[2]: row for row in publications}
    generations = {library: row.generation for library, row in publication_by_library.items()}
    coordinators = {
        row.library: row
        for row in (
            await session.scalars(
                select(TasteProfileCoordinator).where(
                    TasteProfileCoordinator.library.in_(_PROFILE_LIBRARIES)
                )
            )
        ).all()
    }
    libraries: dict[str, dict[str, Any]] = {}
    for library in _PROFILE_LIBRARIES:
        publication, active_revision_digest = await _active_profile_details(session, library)
        coordinator = coordinators.get(library)
        desired_revision = coordinator.desired_revision_digest if coordinator is not None else None
        inflight = (
            await session.get(TasteProfileBuild, coordinator.inflight_build_id)
            if coordinator is not None and coordinator.inflight_build_id is not None
            else None
        )
        latest_statement = select(TasteProfileBuild).where(TasteProfileBuild.library == library)
        if desired_revision is not None:
            latest_statement = latest_statement.where(
                TasteProfileBuild.revision_digest == desired_revision
            )
        latest = await session.scalar(
            latest_statement
            .order_by(
                TasteProfileBuild.completed_at.desc().nullslast(),
                TasteProfileBuild.created_at.desc(),
                TasteProfileBuild.id.desc(),
            )
            .limit(1)
        )
        build = inflight or latest
        active_build = (
            await session.scalar(
                select(TasteProfileBuild)
                .where(
                    TasteProfileBuild.library == library,
                    TasteProfileBuild.state.in_(("succeeded", "no_change")),
                    TasteProfileBuild.result_checksum
                    == (publication.checksum if publication is not None else None),
                )
                .order_by(TasteProfileBuild.completed_at.desc(), TasteProfileBuild.id.desc())
                .limit(1)
            )
            if publication is not None
            else None
        )
        legacy_revision = (
            await session.get(TasteProfileRevision, active_revision_digest)
            if active_revision_digest is not None
            else None
        )
        legacy_reload = (
            legacy_revision.consumer_reload.get(library)
            if legacy_revision is not None and isinstance(legacy_revision.consumer_reload, dict)
            else None
        )
        reload_checksum = (
            active_build.consumer_reload_checksum if active_build is not None else legacy_reload
        )
        active_checksum = publication.checksum if publication is not None else None
        reload_ready = bool(active_checksum and reload_checksum == active_checksum)
        active = {
            "generation": publication.generation if publication is not None else None,
            "checksum": active_checksum,
            "revision": active_revision_digest,
            "compatible": bool(publication is not None and active_revision_digest and reload_ready),
        }
        residual_publication = await session.get(
            MlActivePublication, f"ranking_residual:{library}"
        )
        residual_artifact = (
            await session.get(JobArtifact, residual_publication.artifact_id)
            if residual_publication is not None
            else None
        )
        residual_metadata = (
            residual_artifact.artifact_metadata if residual_artifact is not None else None
        )
        residual_compatible = bool(
            residual_publication is not None
            and isinstance(residual_metadata, dict)
            and residual_metadata.get("profile_checksum") == active_checksum
            and residual_metadata.get("profile_generation") == active["generation"]
        )
        residual = {
            "active": residual_publication is not None,
            "compatible": residual_compatible,
            "dormant": bool(residual_publication is not None and not residual_compatible),
        }
        build_state = {
            "id": build.id if build is not None else None,
            "job_id": build.job_id if build is not None else None,
            "state": build.state if build is not None else None,
            "revision": build.revision_digest if build is not None else None,
            "expected_generation": build.expected_generation if build is not None else None,
            "retry_of": build.supersedes_build_id if build is not None else None,
            "failure": build.failure if build is not None else None,
        }
        desired_for_due = desired_revision or active_revision_digest
        libraries[library] = {
            "active": active,
            "desired_revision": desired_revision,
            "desired_generation": coordinator.desired_generation if coordinator is not None else None,
            "build": build_state,
            "reload_state": {
                "expected_checksum": active_checksum,
                "observed_checksum": reload_checksum,
                "ready": reload_ready,
            },
            "residual": residual,
            "rebuild_due": bool(desired_for_due and desired_for_due != active_revision_digest),
            "update_attention": bool(
                active["compatible"]
                and build is not None
                and build.state in {"failed", "cancelled", "superseded"}
            ),
        }
    initial_profiles_ready = all(
        libraries[library]["active"]["compatible"] for library in _PROFILE_LIBRARIES
    )
    consumer_reloaded = initial_profiles_ready
    rebuild_due = any(libraries[library]["rebuild_due"] for library in _PROFILE_LIBRARIES)
    active_builds = any(
        libraries[library]["build"]["state"] in _INFLIGHT_PROFILE_BUILD_STATES
        for library in _PROFILE_LIBRARIES
    )
    failures = [
        libraries[library]["build"]["failure"]
        for library in _PROFILE_LIBRARIES
        if libraries[library]["build"]["state"] in {"failed", "cancelled", "superseded"}
        and libraries[library]["build"]["failure"] is not None
    ]
    count = len(positives)
    if initial_profiles_ready:
        state, next_action = "personalized", "continue refining taste"
    elif count >= thresholds.required and active_builds:
        state, next_action = "building", "wait for profile validation and reload"
    elif count >= thresholds.required and failures:
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
        build_revision=next(
            (
                libraries[library]["desired_revision"]
                for library in _PROFILE_LIBRARIES
                if libraries[library]["desired_revision"] is not None
            ),
            None,
        ),
        build_job_id=next(
            (
                libraries[library]["build"]["job_id"]
                for library in _PROFILE_LIBRARIES
                if libraries[library]["build"]["job_id"] is not None
            ),
            None,
        ),
        profile_generations=generations,
        consumer_reloaded=consumer_reloaded,
        next_action=next_action,
        failure=failures[0] if failures else None,
        libraries=libraries,
        initial_profiles_ready=initial_profiles_ready,
        personalized_scoring_available=initial_profiles_ready,
        rebuild_due=rebuild_due,
        residual_dormant=any(
            libraries[library]["residual"]["dormant"] for library in _PROFILE_LIBRARIES
        ),
    )
