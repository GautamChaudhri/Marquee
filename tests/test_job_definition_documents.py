from __future__ import annotations

from dataclasses import replace
from typing import Literal

import pytest
from pydantic import ValidationError

from marquee.core.jobs.contracts import (
    AttentionReason,
    EffectSafety,
    ExecutionClass,
    FeatureArea,
    MigrationState,
)
from marquee.core.jobs.definitions import (
    DisabledJobDefinitionError,
    DuplicateJobDefinitionError,
    InvalidJobDefinitionError,
    JobDefinition,
    JobDefinitionRegistry,
    TimeoutPolicy,
    UnknownJobDefinitionError,
)
from marquee.core.jobs.documents import (
    DocumentAdapter,
    DocumentKind,
    EmptyDocumentV1,
    PosterPipelineGroupResultV1,
    SafeJobErrorV1,
    StrictDocument,
    UnsupportedDocumentVersionError,
    current_adapter,
)
from marquee.core.jobs.manifest import JOB_DEFINITION_REGISTRY
from marquee.core.jobs.policies import default_failure_classifier
from marquee.core.jobs.terminal_decision import JobOutcome, TerminalDecisionPolicy


class RequestV1(StrictDocument):
    name: str


class RequestV2(StrictDocument):
    display_name: str


class ResultV1(StrictDocument):
    outcome: Literal["succeeded"] = "succeeded"


def _definition(**updates) -> JobDefinition:
    empty_request = current_adapter(DocumentKind.REQUEST, EmptyDocumentV1)
    empty_result = current_adapter(DocumentKind.RESULT, ResultV1)
    safe_error = current_adapter(DocumentKind.ERROR, SafeJobErrorV1)
    definition = JobDefinition(
        job_type="system_noop",
        label_key="jobs.system_noop",
        feature_area=FeatureArea.SYSTEM,
        presentation_family="system",
        presenter_key="system.noop",
        enabled=True,
        migration_state=MigrationState.ENABLED,
        disabled_reason=None,
        request=empty_request,
        result=empty_result,
        error=safe_error,
        execution_class=ExecutionClass.CONTROL,
        entrypoint="control",
        timeout=TimeoutPolicy(seconds=30),
        effect_safety=EffectSafety.READ_ONLY,
        terminal_policy=TerminalDecisionPolicy.for_result_model(ResultV1),
        failure_classifier=default_failure_classifier,
        configuration_audit="audited_empty",
    )
    return replace(definition, **updates)


def test_current_documents_reject_extra_fields_and_future_versions() -> None:
    adapter = current_adapter(DocumentKind.REQUEST, RequestV1)
    assert adapter.validate({"name": "Marquee"}, version=1) == RequestV1(name="Marquee")
    with pytest.raises(ValidationError):
        adapter.validate({"name": "Marquee", "timeout": 1}, version=1)
    with pytest.raises(UnsupportedDocumentVersionError) as exc_info:
        adapter.validate({"name": "Marquee"}, version=2)
    assert exc_info.value.version == 2
    assert exc_info.value.kind == DocumentKind.REQUEST


def test_only_semantically_real_upcasters_are_declared() -> None:
    adapter = DocumentAdapter(
        kind=DocumentKind.REQUEST,
        current_version=2,
        models={1: RequestV1, 2: RequestV2},
        upcasters={1: lambda old: {"display_name": old.name}},
    )
    assert adapter.validate({"name": "Marquee"}, version=1) == RequestV2(display_name="Marquee")
    assert adapter.upcasters.keys() == {1}


def test_safe_error_document_is_bounded_and_strict() -> None:
    value = SafeJobErrorV1(code="provider_unavailable", summary="Provider unavailable")
    assert value.remediation is None
    with pytest.raises(ValidationError):
        SafeJobErrorV1(
            code="provider_unavailable",
            summary="Provider unavailable",
            environment={"TOKEN": "secret"},
        )
    with pytest.raises(ValidationError):
        SafeJobErrorV1(
            code="provider_unavailable",
            summary="Provider unavailable",
            diagnostics={"callback_token": "secret"},
        )
    with pytest.raises(ValidationError):
        SafeJobErrorV1(
            code="provider_unavailable",
            summary="Provider unavailable",
            diagnostics={"source": "/unconfined/private/file"},
        )


def test_registry_validates_dispatch_and_disabled_contracts() -> None:
    registry = JobDefinitionRegistry([_definition()])
    assert registry.get("system_noop").entrypoint == "control"
    assert registry.types == {"system_noop"}
    assert registry.enabled_types == {"system_noop"}
    assert registry.for_dispatch("system_noop", entrypoint="control").enabled
    registry.validate_coverage({"system_noop"})
    with pytest.raises(UnknownJobDefinitionError):
        registry.get("missing")
    with pytest.raises(DuplicateJobDefinitionError):
        JobDefinitionRegistry([_definition(), _definition()])
    with pytest.raises(InvalidJobDefinitionError, match="coverage mismatch"):
        registry.validate_coverage({"system_noop", "poster_pipeline"})
    disabled = JobDefinitionRegistry(
        [
            _definition(
                job_type="poster_pipeline",
                enabled=False,
                migration_state=MigrationState.DEFINED_DISABLED,
                disabled_reason="handler migration is deferred",
            )
        ]
    )
    with pytest.raises(DisabledJobDefinitionError, match="dispatch-disabled"):
        disabled.for_dispatch("poster_pipeline", entrypoint="gpu")
    # A read-only, non-media-write, ENABLED definition may be dispatch-enabled (chunk 4).
    read_only_enabled = JobDefinitionRegistry([_definition(job_type="library_sync", enabled=True)])
    assert read_only_enabled.for_dispatch("library_sync", entrypoint="control").enabled
    # Chunk 5 mutations require the complete typed/retry/safety contract.
    with pytest.raises(InvalidJobDefinitionError, match="retry policy"):
        JobDefinitionRegistry(
            [
                _definition(
                    job_type="poster_pipeline",
                    enabled=True,
                    effect_safety=EffectSafety.UNSAFE_MUTATION,
                )
            ]
        )
    # A supposedly read-only definition cannot claim the media-write class.
    with pytest.raises(InvalidJobDefinitionError, match="media_write"):
        JobDefinitionRegistry(
            [
                _definition(
                    job_type="synthetic_media_write",
                    enabled=True,
                    execution_class=ExecutionClass.MEDIA_WRITE,
                    entrypoint="media_write",
                )
            ]
        )
    with pytest.raises(InvalidJobDefinitionError, match="disabled definitions require"):
        JobDefinitionRegistry(
            [
                _definition(
                    enabled=False,
                    migration_state=MigrationState.DEFINED_DISABLED,
                )
            ]
        )


def test_clients_have_no_fields_for_server_execution_policy() -> None:
    fields = RequestV1.model_fields
    assert not fields.keys() & {
        "entrypoint",
        "timeout",
        "retry_policy",
        "effect_safety",
        "progress_policy",
        "actions",
    }


def test_review_required_carries_a_review_reason_not_a_failure_reason() -> None:
    """`review_required` borrows `partially_succeeded`; it must not read as failure."""
    definition = JOB_DEFINITION_REGISTRY.get("poster_pipeline_group")
    policy = definition.terminal_policy
    assert policy.review_outcomes == frozenset({"review_required"})

    document = PosterPipelineGroupResultV1(
        outcome="review_required",
        library="movies",
        chunk_index=0,
        member_count=1,
        review_required_count=1,
        projected_count=1,
        run_ids=("a" * 32,),
        message="1 poster selection(s) are ready for your review.",
    )
    decision = policy.decide(document, job_type="poster_pipeline_group")
    assert decision.job_outcome is JobOutcome.PARTIALLY_SUCCEEDED
    assert decision.attention_document()["reason"] == AttentionReason.REVIEW.value

    clean = PosterPipelineGroupResultV1(
        outcome="succeeded",
        library="movies",
        chunk_index=0,
        member_count=1,
        succeeded_count=1,
        projected_count=1,
        run_ids=("a" * 32,),
    )
    assert policy.decide(clean, job_type="poster_pipeline_group").attention_reason is None

    # The contract folds failures and review into one outcome value, so a group
    # that only failed must not be dressed up as ready for review.
    failed = PosterPipelineGroupResultV1(
        outcome="review_required",
        library="movies",
        chunk_index=0,
        member_count=1,
        failed_count=1,
        failed_subject_keys=("movie:11",),
        projected_count=1,
        run_ids=("a" * 32,),
    )
    decision = policy.decide(failed, job_type="poster_pipeline_group")
    assert decision.attention_reason is None
    assert decision.attention_document()["reason"] == "failed"
