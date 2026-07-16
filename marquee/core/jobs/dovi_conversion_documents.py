"""Path-free canonical documents for Dolby Vision conversion and publication."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from marquee.core.jobs.documents import StrictDocument
from marquee.core.jobs.letterbox_reencode_documents import ReencodeProbeV1


class DoviProbeV1(ReencodeProbeV1):
    dovi_profile: int | None = Field(default=None, ge=1, le=10)
    dovi_level: int | None = Field(default=None, ge=0, le=255)
    enhancement_layer_present: bool | None = None
    bl_signal_compatibility_id: int | None = Field(default=None, ge=0, le=15)


class DoviConvertRequestV1(StrictDocument):
    media_file_id: int = Field(gt=0)
    movie_id: int = Field(gt=0)
    kind: Literal["p5_to_p81", "p7_strip_el"]
    source_signature: str = Field(min_length=1, max_length=200)
    source_size_bytes: int = Field(ge=1)
    source_probe: DoviProbeV1
    source_el_type: Literal["MEL", "FEL"] | None = None

    @model_validator(mode="after")
    def validate_conversion(self) -> DoviConvertRequestV1:
        expected = 5 if self.kind == "p5_to_p81" else 7
        if self.source_probe.dovi_profile != expected:
            raise ValueError("conversion kind does not match the sealed Dolby Vision profile")
        if self.kind == "p7_strip_el" and self.source_el_type not in {"MEL", "FEL"}:
            raise ValueError("Profile 7 conversion requires a sealed enhancement-layer type")
        return self


class DoviConvertResultV1(StrictDocument):
    outcome: Literal["succeeded", "failed", "cancelled"]
    reason_code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=500)
    kind: Literal["p5_to_p81", "p7_strip_el"]
    artifact_id: int | None = Field(default=None, gt=0)
    artifact_checksum: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    artifact_size_bytes: int | None = Field(default=None, ge=1)
    source_profile: int | None = Field(default=None, ge=1, le=10)
    target_profile: int = Field(default=8, ge=1, le=10)
    source_probe: DoviProbeV1 | None = None
    output_probe: DoviProbeV1 | None = None
    validated: bool = False
    original_untouched: bool = True
    validation: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_candidate(self) -> DoviConvertResultV1:
        if self.outcome == "succeeded" and (
            self.artifact_id is None
            or self.artifact_checksum is None
            or self.artifact_size_bytes is None
            or self.source_probe is None
            or self.output_probe is None
            or not self.validated
        ):
            raise ValueError("successful conversion requires candidate and probe evidence")
        return self


class DoviPublishRequestV1(StrictDocument):
    media_file_id: int = Field(gt=0)
    movie_id: int = Field(gt=0)
    candidate_artifact_id: int = Field(gt=0)
    candidate_job_id: str = Field(min_length=1, max_length=64)
    candidate_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_size_bytes: int = Field(ge=1)
    expected_source_signature: str = Field(min_length=1, max_length=200)
    source_probe: DoviProbeV1
    candidate_probe: DoviProbeV1


class DoviRestoreRequestV1(StrictDocument):
    media_file_id: int = Field(gt=0)
    movie_id: int = Field(gt=0)
    candidate_artifact_id: int = Field(gt=0)
    backup_artifact_id: int = Field(gt=0)
    backup_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    backup_size_bytes: int = Field(ge=1)
    expected_destination_signature: str = Field(min_length=1, max_length=200)
    published_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")


class DoviDiscardRequestV1(StrictDocument):
    media_file_id: int = Field(gt=0)
    movie_id: int = Field(gt=0)
    candidate_artifact_id: int = Field(gt=0)
    candidate_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")


class DoviDecisionResultV1(StrictDocument):
    outcome: Literal["succeeded", "no_change", "failed", "unsafe", "cancelled"]
    operation: Literal["publish", "restore", "discard"]
    reason_code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=500)
    media_file_id: int = Field(gt=0)
    movie_id: int = Field(gt=0)
    candidate_artifact_id: int = Field(gt=0)
    backup_artifact_id: int | None = Field(default=None, gt=0)
    before_signature: str | None = Field(default=None, max_length=200)
    actual_signature: str | None = Field(default=None, max_length=200)
    actual_checksum: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    actual_probe: DoviProbeV1 | None = None
    rescanned: bool = False
    reconciled: bool = False
    candidate_discarded: bool = False
