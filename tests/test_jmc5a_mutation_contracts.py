from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import ValidationError

from marquee.core.jobs.contracts import MigrationState
from marquee.core.jobs.definitions import InvalidJobDefinitionError, JobDefinitionRegistry
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
    publication_reconciliation_state,
    require_publication_preconditions,
)
from marquee.core.jobs.mutation_evidence import apply_mutation_evidence, read_mutation_evidence
from marquee.core.jobs.presenters import load_context, resolve_presenter
from marquee.models import MediaOperationDetail
from tests.test_job_presenters import make_job


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
    assert {item.status for item in result.target_outcomes} == {
        MutationTargetStatus.NOT_APPLIED
    }
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
    assert publication_reconciliation_state(
        intent_recorded=True, publication_recorded=False
    ) == "required"
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
    mutation = next(section for section in presentation.sections if section.title == "Mutation evidence")
    assert [fact.label for fact in mutation.facts] == ["Validation", "Atomicity", "Published"]

    detail.validation = {"verdict": "invented"}
    context = load_context(job, definition, mutation_detail=detail)
    assert context.mutation is None
    assert any(warning.code == "malformed_evidence" for warning in context.warnings)


def test_enabled_mutations_cannot_use_generic_builtin_documents() -> None:
    deferred = JOB_DEFINITION_REGISTRY.get("dovi_convert")
    generic_enabled = replace(
        deferred,
        enabled=True,
        migration_state=MigrationState.ENABLED,
        disabled_reason=None,
    )
    with pytest.raises(InvalidJobDefinitionError, match="family-specific"):
        JobDefinitionRegistry((generic_enabled,))
