"""Path-free documents for permanent letterbox re-encode candidate jobs."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from marquee.core.jobs.documents import StrictDocument


class ReencodeSourceSnapshotV1(StrictDocument):
    signature: str = Field(min_length=1, max_length=200)
    size_bytes: int = Field(ge=1)
    codec: str | None = Field(default=None, max_length=40)
    width: int = Field(gt=0, le=32_768)
    height: int = Field(gt=0, le=32_768)
    duration_seconds: float | None = Field(default=None, gt=0)
    pixel_format: str | None = Field(default=None, max_length=40)
    color_transfer: str | None = Field(default=None, max_length=40)
    color_primaries: str | None = Field(default=None, max_length=40)
    color_space: str | None = Field(default=None, max_length=40)
    has_hdr: bool
    has_dolby_vision: bool
    video_streams: int = Field(ge=1, le=32)
    audio_streams: int = Field(ge=0, le=256)
    subtitle_streams: int = Field(ge=0, le=256)
    attachment_streams: int = Field(ge=0, le=256)


class ReencodeEncoderPlanV1(StrictDocument):
    codec: Literal["h264", "hevc"]
    encoder: str = Field(min_length=1, max_length=80)
    family: Literal["nvidia", "intel_qsv", "intel_vaapi", "cpu"]
    quality: int = Field(ge=0, le=51)
    preset: str | None = Field(default=None, max_length=80)
    used_cpu_fallback: bool


class LetterboxReencodeRequestV1(StrictDocument):
    media_file_id: int = Field(gt=0)
    subject_kind: Literal["movie", "episode"]
    subject_id: int = Field(gt=0)
    crop_top: int = Field(ge=0, le=32_768)
    crop_bottom: int = Field(ge=0, le=32_768)
    output_height: int = Field(gt=0, le=32_768)
    source: ReencodeSourceSnapshotV1
    encoder: ReencodeEncoderPlanV1

    @model_validator(mode="after")
    def validate_crop(self) -> LetterboxReencodeRequestV1:
        if self.crop_top + self.crop_bottom <= 0:
            raise ValueError("a permanent re-encode requires a non-zero crop")
        if self.source.height - self.crop_top - self.crop_bottom != self.output_height:
            raise ValueError("output height does not match the sealed crop")
        return self


class ReencodeProbeV1(StrictDocument):
    codec: str | None = Field(default=None, max_length=40)
    width: int = Field(gt=0, le=32_768)
    height: int = Field(gt=0, le=32_768)
    duration_seconds: float | None = Field(default=None, gt=0)
    has_hdr: bool
    has_dolby_vision: bool
    video_streams: int = Field(ge=1, le=32)
    audio_streams: int = Field(ge=0, le=256)
    subtitle_streams: int = Field(ge=0, le=256)
    attachment_streams: int = Field(ge=0, le=256)


class LetterboxReencodeResultV1(StrictDocument):
    outcome: Literal["succeeded", "failed", "cancelled"]
    reason_code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=500)
    artifact_id: int | None = Field(default=None, gt=0)
    artifact_checksum: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    artifact_size_bytes: int | None = Field(default=None, ge=1)
    crop_top: int = Field(ge=0, le=32_768)
    crop_bottom: int = Field(ge=0, le=32_768)
    encoder: str = Field(min_length=1, max_length=80)
    hardware_family: str = Field(min_length=1, max_length=40)
    used_cpu_fallback: bool
    source_width: int | None = Field(default=None, gt=0, le=32_768)
    source_height: int | None = Field(default=None, gt=0, le=32_768)
    output_width: int | None = Field(default=None, gt=0, le=32_768)
    output_height: int | None = Field(default=None, gt=0, le=32_768)
    input_bytes: int | None = Field(default=None, ge=1)
    output_bytes: int | None = Field(default=None, ge=1)
    validated: bool = False
    hdr_preserved: bool | None = None
    elapsed_seconds: float | None = Field(default=None, ge=0)
    speed: float | None = Field(default=None, ge=0)
    source_probe: ReencodeProbeV1 | None = None
    output_probe: ReencodeProbeV1 | None = None
    validation: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_candidate_evidence(self) -> LetterboxReencodeResultV1:
        if self.outcome == "succeeded" and (
            self.artifact_id is None
            or self.artifact_checksum is None
            or self.artifact_size_bytes is None
            or self.source_probe is None
            or self.output_probe is None
        ):
            raise ValueError("successful re-encode results require candidate and probe evidence")
        return self


class LetterboxReencodePublishRequestV1(StrictDocument):
    media_file_id: int = Field(gt=0)
    subject_kind: Literal["movie", "episode"]
    subject_id: int = Field(gt=0)
    candidate_artifact_id: int = Field(gt=0)
    candidate_job_id: str = Field(min_length=1, max_length=64)
    candidate_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_size_bytes: int = Field(ge=1)
    expected_source_signature: str = Field(min_length=1, max_length=200)
    crop_top: int = Field(ge=0, le=32_768)
    crop_bottom: int = Field(ge=0, le=32_768)
    source_probe: ReencodeProbeV1
    candidate_probe: ReencodeProbeV1


class LetterboxReencodeRestoreRequestV1(StrictDocument):
    media_file_id: int = Field(gt=0)
    subject_kind: Literal["movie", "episode"]
    subject_id: int = Field(gt=0)
    candidate_artifact_id: int = Field(gt=0)
    backup_artifact_id: int = Field(gt=0)
    backup_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    backup_size_bytes: int = Field(ge=1)
    expected_destination_signature: str = Field(min_length=1, max_length=200)
    published_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")


class LetterboxReencodeDiscardRequestV1(StrictDocument):
    media_file_id: int = Field(gt=0)
    subject_kind: Literal["movie", "episode"]
    subject_id: int = Field(gt=0)
    candidate_artifact_id: int = Field(gt=0)
    candidate_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")


class LetterboxReencodeDecisionResultV1(StrictDocument):
    outcome: Literal["succeeded", "no_change", "failed", "unsafe", "cancelled"]
    operation: Literal["publish", "restore", "discard"]
    reason_code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=500)
    media_file_id: int = Field(gt=0)
    candidate_artifact_id: int = Field(gt=0)
    backup_artifact_id: int | None = Field(default=None, gt=0)
    before_signature: str | None = Field(default=None, max_length=200)
    actual_signature: str | None = Field(default=None, max_length=200)
    actual_checksum: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    rescanned: bool = False
    reconciled: bool = False
    candidate_discarded: bool = False
