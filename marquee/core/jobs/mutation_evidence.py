"""Validated persistence/read boundary for canonical mutation evidence."""

from __future__ import annotations

from marquee.core.jobs.mutation_documents import MutationEvidenceV1
from marquee.models.job_evidence import MediaOperationDetail


def dump_mutation_evidence(evidence: MutationEvidenceV1) -> dict[str, object]:
    """Return the exact JSON-column payload after one strict validation pass."""
    value = MutationEvidenceV1.model_validate(evidence)
    return value.model_dump(mode="json", exclude_none=True)


def apply_mutation_evidence(
    detail: MediaOperationDetail, evidence: MutationEvidenceV1
) -> MediaOperationDetail:
    """Populate all mutation JSON fields together; callers commit the surrounding transaction."""
    payload = dump_mutation_evidence(evidence)
    detail.requested_target = payload["requested_target"]
    detail.expected_target = payload.get("expected_target")
    detail.actual_target = payload.get("actual_target")
    detail.validation = payload["validation"]
    detail.atomicity = payload["atomicity"]
    detail.backup = payload.get("backup")
    detail.publish = payload.get("publish")
    return detail


def read_mutation_evidence(detail: MediaOperationDetail) -> MutationEvidenceV1:
    """Strict API/presenter read validation; malformed canonical evidence is never fabricated."""
    return MutationEvidenceV1.model_validate(
        {
            "requested_target": detail.requested_target,
            "expected_target": detail.expected_target,
            "actual_target": detail.actual_target,
            "validation": detail.validation,
            "atomicity": detail.atomicity,
            "backup": detail.backup,
            "publish": detail.publish,
        }
    )
