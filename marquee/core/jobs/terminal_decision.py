"""Definition-owned terminal mapping for validated handler results."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel


class JobOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    NO_CHANGE = "no_change"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    DEAD_LETTER = "dead_letter"
    UNSAFE = "unsafe"


class AttemptOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    RETRYING = "retrying"


class DispatchDisposition(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STALE = "stale"
    SUPERSEDED = "superseded"
    DEAD_LETTERED = "dead_lettered"


class WorkspaceDisposition(StrEnum):
    CLEAN = "clean"
    QUARANTINE = "quarantine"


class AttentionSeverity(StrEnum):
    NONE = "none"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class TerminalDecision:
    job_outcome: JobOutcome
    attempt_outcome: AttemptOutcome
    dispatch_disposition: DispatchDisposition
    acknowledge_transport: bool
    workspace: WorkspaceDisposition
    attention: AttentionSeverity
    summary: str
    remediation: str | None = None
    # Overrides the outcome-derived reason. Kept as a bare string so this module
    # stays free of any dependency on the presentation contracts.
    attention_reason: str | None = None

    def attention_document(self) -> dict[str, str | None]:
        reason = "none"
        if self.attention != AttentionSeverity.NONE and self.attention_reason is not None:
            reason = self.attention_reason
        elif self.job_outcome == JobOutcome.UNSAFE:
            reason = "unsafe"
        elif self.attention != AttentionSeverity.NONE:
            reason = "failed"
        return {
            "level": "normal" if self.attention == AttentionSeverity.NONE else self.attention.value,
            "reason": reason,
            "message": self.summary if self.attention != AttentionSeverity.NONE else None,
            "remediation": self.remediation,
        }


class InvalidTerminalMappingError(ValueError):
    """A result model or validated result has no closed terminal mapping."""


def _closed_outcomes(annotation: Any) -> frozenset[str]:
    values: set[str] = set()
    origin = get_origin(annotation)
    if origin is Literal:
        values.update(str(value) for value in get_args(annotation))
    elif isinstance(annotation, type) and issubclass(annotation, Enum):
        values.update(str(member.value) for member in annotation)
    else:
        for item in get_args(annotation):
            values.update(_closed_outcomes(item))
    return frozenset(values)


@dataclass(frozen=True, slots=True)
class TerminalDecisionPolicy:
    """Closed mapping owned by a definition's versioned result contract."""

    outcome_mapping: tuple[tuple[str, JobOutcome], ...]
    # Raw result outcomes that mean "finished cleanly, awaiting a human
    # decision". They keep their canonical alias (and its attention severity)
    # but must not be reported as failure.
    review_outcomes: frozenset[str] = frozenset()

    @property
    def result_outcomes(self) -> frozenset[str]:
        return frozenset(raw for raw, _canonical in self.outcome_mapping)

    @staticmethod
    def result_outcomes_for_model(model: type[BaseModel]) -> frozenset[str]:
        field = model.model_fields.get("outcome")
        if field is None:
            raise InvalidTerminalMappingError(f"{model.__name__} has no outcome field")
        outcomes = _closed_outcomes(field.annotation)
        if not outcomes:
            raise InvalidTerminalMappingError(
                f"{model.__name__}.outcome is not a closed Literal or enum"
            )
        return outcomes

    @classmethod
    def for_result_model(
        cls,
        model: type[BaseModel],
        *,
        aliases: Mapping[str, JobOutcome] | None = None,
        review_outcomes: frozenset[str] = frozenset(),
    ) -> TerminalDecisionPolicy:
        outcomes = cls.result_outcomes_for_model(model)
        aliases = aliases or {}
        mapping: list[tuple[str, JobOutcome]] = []
        for raw in sorted(outcomes):
            try:
                canonical = aliases[raw] if raw in aliases else JobOutcome(raw)
            except ValueError as exc:
                raise InvalidTerminalMappingError(
                    f"{model.__name__}.outcome {raw!r} requires an explicit canonical mapping"
                ) from exc
            mapping.append((raw, canonical))
        if set(aliases) - outcomes:
            raise InvalidTerminalMappingError("terminal aliases include outcomes outside the model")
        if review_outcomes - outcomes:
            raise InvalidTerminalMappingError(
                "terminal review outcomes include outcomes outside the model"
            )
        return cls(outcome_mapping=tuple(mapping), review_outcomes=review_outcomes)

    def decide(self, document: BaseModel, *, job_type: str) -> TerminalDecision:
        raw_outcome = getattr(document, "outcome", None)
        value = raw_outcome.value if isinstance(raw_outcome, Enum) else raw_outcome
        mapping = dict(self.outcome_mapping)
        if value not in mapping:
            raise InvalidTerminalMappingError(
                f"{job_type} returned outcome outside its result contract: {value!r}"
            )
        outcome = mapping[value]

        attempt = AttemptOutcome.SUCCEEDED
        dispatch = DispatchDisposition.SUCCEEDED
        workspace = WorkspaceDisposition.CLEAN
        attention = AttentionSeverity.NONE
        if outcome == JobOutcome.CANCELLED:
            attempt = AttemptOutcome.CANCELLED
            dispatch = DispatchDisposition.CANCELLED
            workspace = WorkspaceDisposition.QUARANTINE
        elif outcome == JobOutcome.SUPERSEDED:
            dispatch = DispatchDisposition.SUPERSEDED
        elif outcome == JobOutcome.PARTIALLY_SUCCEEDED:
            attention = AttentionSeverity.WARNING
        elif outcome == JobOutcome.FAILED:
            attention = AttentionSeverity.ERROR
        elif outcome == JobOutcome.UNSAFE:
            attention = AttentionSeverity.ERROR
            workspace = WorkspaceDisposition.QUARANTINE
        elif outcome == JobOutcome.DEAD_LETTER:
            attempt = AttemptOutcome.FAILED
            dispatch = DispatchDisposition.DEAD_LETTERED
            attention = AttentionSeverity.ERROR
            workspace = WorkspaceDisposition.QUARANTINE

        message = getattr(document, "message", None)
        reason = getattr(document, "reason_code", None)
        summary = str(message or reason or f"{job_type} {outcome.value}")[:1000]
        return TerminalDecision(
            job_outcome=outcome,
            attempt_outcome=attempt,
            dispatch_disposition=dispatch,
            acknowledge_transport=True,
            workspace=workspace,
            attention=attention,
            summary=summary,
            # A result contract may fold "some members failed" and "some members
            # want your pick" into one outcome value; `failed_count` is the only
            # thing that tells them apart. Same test the batch projector uses, so
            # a parent and its child cannot label the same run differently.
            attention_reason=(
                "review"
                if value in self.review_outcomes and not getattr(document, "failed_count", 0)
                else None
            ),
        )
