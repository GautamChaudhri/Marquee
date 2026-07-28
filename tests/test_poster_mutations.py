"""Poster mutations: the fenced write path and its outcome contracts.

Deploy, reset, restore, and backup are the only writers of library artwork. Covers
deterministic idempotency keys, the deploy/no-change/reset/restore round trip for movies
and TV, poisoned-projection rejection, and the outcome contracts every mutation reports —
no-change with reasons, all-or-nothing failure, partial groups, and stale fences."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image
from pydantic import ValidationError

from marquee.config import settings
from marquee.core.jobs.contracts import MigrationState
from marquee.core.jobs.definitions import InvalidJobDefinitionError, JobDefinitionRegistry
from marquee.core.jobs.documents import (
    BuiltInIntentV1,
    BuiltInResultV1,
    DocumentKind,
    current_adapter,
)
from marquee.core.jobs.execution_io import ExecutionIO
from marquee.core.jobs.handlers_poster_mutations import (
    PosterMutationError,
    _boundary,
    _execute_copy,
    execute_poster_backup_subject,
    execute_poster_reset,
    execute_poster_restore,
)
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.mutation_documents import (
    MutationAtomicityV1,
    MutationEvidenceV1,
    MutationJobOutcome,
    MutationPreconditionError,
    MutationResultV1,
    MutationSnapshotV1,
    MutationTargetOutcomeV1,
    MutationTargetStatus,
    MutationTargetV1,
    MutationValidationV1,
    PosterCandidateSelectionV1,
    PosterDeployRequestV1,
    publication_reconciliation_state,
    require_publication_preconditions,
)
from marquee.core.jobs.mutation_evidence import apply_mutation_evidence, read_mutation_evidence
from marquee.core.jobs.poster_submission import poster_child_idempotency_key
from marquee.core.jobs.presenters import load_context, resolve_presenter
from marquee.core.jobs.publication import file_signature
from marquee.core.poster_subjects import PosterSubject
from marquee.database import _get_session_factory
from marquee.models import Job, MediaOperationDetail, Movie, Season, Series
from tests.test_job_presenters import make_job


def test_poster_child_idempotency_key_is_deterministic_and_path_free():
    first = poster_child_idempotency_key("bulk:one", "series/../../42")
    assert first == poster_child_idempotency_key("bulk:one", "series/../../42")
    assert first.startswith("poster_deploy:")
    assert "/" not in first


class _Fence:
    def __init__(self) -> None:
        self.intents: list[dict] = []
        self.publications: list[dict] = []

    async def owns_current_attempt(self, _session) -> bool:
        return True

    async def record_publish_intent(self, intent: dict) -> str:
        self.intents.append(intent)
        return "applied"

    async def publish_atomic(self, action, evidence: dict) -> str:
        action()
        self.publications.append(evidence)
        return "applied"


async def _context(db, *, job_type: str, request: dict):
    job_id = uuid4().hex
    db.add(
        Job(
            id=job_id,
            type=job_type,
            payload_version=1,
            request=request,
            phase="running",
            desired_state="run",
            fence_token=7,
            root_id=job_id,
            trigger_kind="manual",
            feature_area="ai_posters",
            presentation_family="ai_posters",
            subject_kind=request["target_kind"],
            subject_reference=str(request["target_id"]),
            subject_snapshot={"version": 1, "kind": request["target_kind"]},
        )
    )
    await db.commit()

    async def owns_fence() -> bool:
        return True

    return SimpleNamespace(
        delivery=SimpleNamespace(canonical_job_id=job_id),
        attempt=SimpleNamespace(attempt_id=1, fence_token=7),
        request=request,
        definition=JOB_DEFINITION_REGISTRY.get(job_type),
        cancellation=SimpleNamespace(cancel_called=False),
        io=ExecutionIO(cancelled=lambda: False, owns_fence=owns_fence),
        writer=_Fence(),
        session_factory=_get_session_factory(),
    )


@pytest.mark.asyncio
async def test_movie_deploy_no_change_reset_restore_round_trip(
    db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media_root = tmp_path / "media"
    movie_folder = media_root / "Movie"
    movie_folder.mkdir(parents=True)
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(media_root)])
    monkeypatch.setattr(settings, "MOVIE_POSTER_FORMAT", "poster.jpg")

    movie = Movie(title="Movie", year=2026, radarr_id=901, folder_path=str(movie_folder))
    db.add(movie)
    await db.commit()

    candidate_path = Path(settings.DATA_DIR) / "poster-tests" / "candidate.jpg"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 48), (12, 34, 56)).save(candidate_path, format="JPEG")

    subject = await db.get(Movie, movie.id)
    boundary, _destination = _boundary(PosterSubject.from_movie(subject))
    candidate = boundary.classify(candidate_path, roots=("data",), require_file=True)
    signature = file_signature(boundary, candidate)
    selection = PosterCandidateSelectionV1(
        source="pipeline_run",
        storage_key="poster-tests/candidate.jpg",
        run_id="run-mutation",
        candidate_reference="candidate.jpg",
        expected_checksum=signature.sha256,
    )
    deploy_request = PosterDeployRequestV1(
        target_kind="movie",
        target_id=movie.id,
        candidate=selection,
        ai_selected=True,
        user_approved=True,
    )

    deploy_context = await _context(
        db, job_type="poster_deploy", request=deploy_request.model_dump(mode="json")
    )
    deployed = await _execute_copy(deploy_context, deploy_request, candidate, "pipeline_run")
    assert deployed["outcome"] == "succeeded"
    assert (movie_folder / "poster.jpg").read_bytes() == candidate_path.read_bytes()

    no_change_context = await _context(
        db, job_type="poster_deploy", request=deploy_request.model_dump(mode="json")
    )
    no_change = await _execute_copy(no_change_context, deploy_request, candidate, "pipeline_run")
    assert no_change["outcome"] == "no_change"
    assert no_change["target_outcomes"][0]["bytes_changed"] is False
    assert no_change["target_outcomes"][0]["product_state_changed"] is False

    reset_request = {"target_kind": "movie", "target_id": movie.id}
    reset_context = await _context(db, job_type="poster_reset", request=reset_request)
    reset = await execute_poster_reset(reset_context)
    assert reset["outcome"] == "succeeded"
    assert not (movie_folder / "poster.jpg").exists()

    restored_movie = await db.get(Movie, movie.id, populate_existing=True)
    assert restored_movie.poster_local_backup_path
    restore_request = {"target_kind": "movie", "target_id": movie.id}
    restore_context = await _context(db, job_type="poster_restore", request=restore_request)
    restored = await execute_poster_restore(restore_context)
    assert restored["outcome"] == "succeeded"
    assert (movie_folder / "poster.jpg").read_bytes() == candidate_path.read_bytes()

    detail = await db.get(MediaOperationDetail, restore_context.delivery.canonical_job_id)
    assert detail.actual_target["checksum"] == signature.sha256
    assert detail.atomicity["published"] is True


@pytest.mark.asyncio
async def test_poster_reset_rejects_poisoned_projection_path(
    db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media_root = tmp_path / "media"
    movie_folder = media_root / "Movie"
    movie_folder.mkdir(parents=True)
    poster = movie_folder / "poster.jpg"
    Image.new("RGB", (16, 24), (1, 2, 3)).save(poster, format="JPEG")
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(media_root)])
    monkeypatch.setattr(settings, "MOVIE_POSTER_FORMAT", "poster.jpg")
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"keep")
    movie = Movie(
        title="Poisoned",
        year=2026,
        radarr_id=902,
        folder_path=str(movie_folder),
        poster_path=str(outside),
    )
    db.add(movie)
    await db.commit()
    request = {"target_kind": "movie", "target_id": movie.id}
    context = await _context(db, job_type="poster_reset", request=request)

    with pytest.raises(PosterMutationError, match="canonical destination"):
        await execute_poster_reset(context)
    assert poster.exists()
    assert outside.read_bytes() == b"keep"


@pytest.mark.asyncio
async def test_poster_backup_subject_copies_once_and_then_reports_no_change(
    db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media_root = tmp_path / "backup-media"
    movie_folder = media_root / "Movie"
    movie_folder.mkdir(parents=True)
    poster = movie_folder / "poster.jpg"
    Image.new("RGB", (16, 24), (7, 8, 9)).save(poster, format="JPEG")
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(media_root)])
    monkeypatch.setattr(settings, "MOVIE_POSTER_FORMAT", "poster.jpg")
    movie = Movie(
        title="Backup",
        year=2026,
        radarr_id=903,
        folder_path=str(movie_folder),
        poster_path=str(poster),
    )
    db.add(movie)
    await db.commit()
    request = {"target_kind": "movie", "target_id": movie.id}

    first = await execute_poster_backup_subject(
        await _context(db, job_type="poster_backup_subject", request=request)
    )
    second = await execute_poster_backup_subject(
        await _context(db, job_type="poster_backup_subject", request=request)
    )

    assert first["outcome"] == "succeeded"
    assert first["backup"]["checksum"] == first["target_outcomes"][0]["actual"]["checksum"]
    assert first["backup"]["size_bytes"] > 0
    assert second["outcome"] == "no_change"
    assert second["reason_code"] == "backup_already_identical"
    assert second["target_outcomes"][0]["bytes_changed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_kind", "filename"), (("series", "poster.jpg"), ("season", "season01.jpg"))
)
async def test_tv_subject_deploy_uses_canonical_leaf(
    db,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_kind: str,
    filename: str,
) -> None:
    media_root = tmp_path / "media"
    show_folder = media_root / "Show"
    show_folder.mkdir(parents=True)
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(media_root)])
    monkeypatch.setattr(settings, "SERIES_POSTER_FORMAT", "poster.jpg")
    monkeypatch.setattr(settings, "SEASON_POSTER_FORMAT", "season{season:02d}.jpg")
    series = Series(title="Show", sonarr_id=950, series_path=str(show_folder))
    db.add(series)
    await db.flush()
    if target_kind == "series":
        target_id = series.id
        subject = PosterSubject.from_series(series)
    else:
        season = Season(series_id=series.id, season_number=1, episode_file_count=1)
        db.add(season)
        await db.flush()
        target_id = season.id
        subject = PosterSubject.from_season(season, series)
    await db.commit()

    candidate_path = Path(settings.DATA_DIR) / "poster-tests" / f"{target_kind}.jpg"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (24, 36), (90, 80, 70)).save(candidate_path, format="JPEG")
    boundary, _destination = _boundary(subject)
    candidate = boundary.classify(candidate_path, roots=("data",), require_file=True)
    signature = file_signature(boundary, candidate)
    request = PosterDeployRequestV1(
        target_kind=target_kind,
        target_id=target_id,
        candidate=PosterCandidateSelectionV1(
            source="pipeline_run",
            storage_key=f"poster-tests/{target_kind}.jpg",
            run_id=f"run-{target_kind}",
            candidate_reference=f"{target_kind}.jpg",
            expected_checksum=signature.sha256,
        ),
    )
    context = await _context(db, job_type="poster_deploy", request=request.model_dump(mode="json"))

    result = await _execute_copy(context, request, candidate, "pipeline_run")

    assert result["outcome"] == "succeeded"
    assert (show_folder / filename).read_bytes() == candidate_path.read_bytes()


def _target(key: str = "movie:42") -> MutationTargetV1:
    return MutationTargetV1(
        key=key,
        kind="movie_artwork",
        label="Example movie",
        operation="poster_deploy",
        selector_facts={"movie_id": 42, "folder_identity": "radarr:42"},
    )


def _snapshot(checksum: str = "a" * 64) -> MutationSnapshotV1:
    return MutationSnapshotV1(
        identity="poster:movie:42", signature="inode:1:2", checksum=checksum, size_bytes=100
    )


def _validation(verdict: str = "passed") -> MutationValidationV1:
    return MutationValidationV1(verdict=verdict)


def _atomic(boundary: str = "single_target", **values) -> MutationAtomicityV1:
    return MutationAtomicityV1(
        group_id="group:42",
        boundary=boundary,
        published=values.get("published", False),
        rollback_available=values.get("rollback_available", True),
        uncertain_state=values.get("uncertain_state", False),
    )


def _outcome(
    target: MutationTargetV1,
    status: MutationTargetStatus,
    *,
    changed: bool = False,
) -> MutationTargetOutcomeV1:
    return MutationTargetOutcomeV1(
        target=target,
        status=status,
        stage="publishing",
        reason_code="candidate_applied" if changed else "not_applied",
        message="Bounded outcome",
        before=_snapshot(),
        expected=_snapshot("b" * 64),
        actual=_snapshot("b" * 64) if changed else _snapshot(),
        bytes_changed=changed,
        product_state_changed=changed,
    )


def test_no_change_is_a_reasoned_job_outcome_without_changed_targets() -> None:
    target = _target()
    result = MutationResultV1(
        outcome="no_change",
        reason_code="already_identical",
        message="The deployed poster already matches the candidate.",
        requested_targets=(target,),
        target_outcomes=(_outcome(target, MutationTargetStatus.SKIPPED),),
        validation=_validation(),
        atomicity=_atomic(),
    )
    assert result.outcome == MutationJobOutcome.NO_CHANGE
    with pytest.raises(ValidationError, match="no_change cannot report applied changes"):
        result.model_copy(
            update={
                "target_outcomes": (_outcome(target, MutationTargetStatus.SUCCEEDED, changed=True),)
            }
        ).__class__.model_validate(
            {
                **result.model_dump(),
                "target_outcomes": [
                    _outcome(target, MutationTargetStatus.SUCCEEDED, changed=True).model_dump()
                ],
            }
        )


def test_failed_all_or_nothing_reports_every_target_not_applied() -> None:
    first, second = _target("movie:1"), _target("movie:2")
    common = {
        "outcome": "failed",
        "reason_code": "validation_failed",
        "message": "No target was published.",
        "requested_targets": (first, second),
        "validation": _validation("failed"),
        "atomicity": _atomic("all_or_nothing"),
    }
    result = MutationResultV1(
        **common,
        target_outcomes=(
            _outcome(first, MutationTargetStatus.NOT_APPLIED),
            _outcome(second, MutationTargetStatus.NOT_APPLIED),
        ),
    )
    assert {item.status for item in result.target_outcomes} == {MutationTargetStatus.NOT_APPLIED}
    with pytest.raises(ValidationError, match="every target not_applied"):
        MutationResultV1(
            **common,
            target_outcomes=(
                _outcome(first, MutationTargetStatus.FAILED),
                _outcome(second, MutationTargetStatus.NOT_APPLIED),
            ),
        )


def test_partial_group_records_only_the_target_that_changed() -> None:
    first, second = _target("movie:1"), _target("movie:2")
    result = MutationResultV1(
        outcome="partially_succeeded",
        reason_code="one_target_failed",
        message="One target published and one failed.",
        requested_targets=(first, second),
        target_outcomes=(
            _outcome(first, MutationTargetStatus.SUCCEEDED, changed=True),
            _outcome(second, MutationTargetStatus.FAILED),
        ),
        validation=_validation("failed"),
        atomicity=_atomic("partial_group", published=True),
    )
    assert [item.bytes_changed for item in result.target_outcomes] == [True, False]


def test_stale_fence_and_unknown_publication_are_never_retry_proof() -> None:
    with pytest.raises(MutationPreconditionError) as stale:
        require_publication_preconditions(
            fence_current=False,
            cancellation_requested=False,
            source_current=True,
            destination_confined=True,
        )
    assert stale.value.code == "stale_fence"
    assert (
        publication_reconciliation_state(intent_recorded=True, publication_recorded=False)
        == "required"
    )
    target = _target()
    unsafe = MutationResultV1(
        outcome="unsafe",
        reason_code="publication_unknown",
        message="Publication requires reconciliation.",
        requested_targets=(target,),
        target_outcomes=(_outcome(target, MutationTargetStatus.FAILED),),
        validation=_validation("not_run"),
        atomicity=_atomic(uncertain_state=True, rollback_available=False),
    )
    assert unsafe.atomicity.uncertain_state is True


def test_media_operation_detail_round_trips_strict_typed_evidence() -> None:
    evidence = MutationEvidenceV1(
        requested_target=_target(),
        expected_target=_snapshot("b" * 64),
        actual_target=_snapshot("b" * 64),
        validation=_validation(),
        atomicity=_atomic(published=True),
    )
    detail = MediaOperationDetail(
        job_id="mutation-evidence-job",
        operation_kind="poster_deploy",
        media_snapshot={},
        target_snapshot={},
        input_signature="sha256:input",
    )
    apply_mutation_evidence(detail, evidence)
    assert read_mutation_evidence(detail) == evidence
    detail.requested_target = {"key": "/operator/path"}
    with pytest.raises(ValidationError):
        read_mutation_evidence(detail)


def test_presenter_exposes_validated_mutation_evidence_and_warns_on_malformed() -> None:
    evidence = MutationEvidenceV1(
        requested_target=_target(),
        expected_target=_snapshot("b" * 64),
        actual_target=_snapshot("b" * 64),
        validation=_validation(),
        atomicity=_atomic(published=True),
    )
    detail = MediaOperationDetail(
        job_id="job0000000000000000000000000001",
        operation_kind="poster_deploy",
        media_snapshot={},
        target_snapshot={},
        input_signature="sha256:input",
    )
    apply_mutation_evidence(detail, evidence)
    job = make_job()
    definition = JOB_DEFINITION_REGISTRY.get(job.type)
    presentation = resolve_presenter(definition).present(
        load_context(job, definition, mutation_detail=detail)
    )
    mutation = next(
        section for section in presentation.sections if section.title == "Mutation evidence"
    )
    assert [fact.label for fact in mutation.facts] == ["Validation", "Atomicity", "Published"]

    detail.validation = {"verdict": "invented"}
    context = load_context(job, definition, mutation_detail=detail)
    assert context.mutation is None
    assert any(warning.code == "malformed_evidence" for warning in context.warnings)


def test_enabled_mutations_cannot_use_generic_builtin_documents() -> None:
    deferred = JOB_DEFINITION_REGISTRY.get("poster_deploy")
    generic_enabled = replace(
        deferred,
        enabled=True,
        migration_state=MigrationState.ENABLED,
        disabled_reason=None,
        request=current_adapter(DocumentKind.REQUEST, BuiltInIntentV1),
        result=current_adapter(DocumentKind.RESULT, BuiltInResultV1),
    )
    with pytest.raises(InvalidJobDefinitionError, match="family-specific"):
        JobDefinitionRegistry((generic_enabled,))
