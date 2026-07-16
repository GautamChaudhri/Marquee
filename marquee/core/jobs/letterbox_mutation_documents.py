"""Typed JMC5C contracts for verified letterbox metadata mutations."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from marquee.core.jobs.documents import StrictDocument
from marquee.core.jobs.mutation_documents import MutationResultV1


class LetterboxSourceSnapshotV1(StrictDocument):
    """Detection and media facts sealed when the mutation is planned."""

    source_signature: str = Field(min_length=1, max_length=200)
    status: str = Field(min_length=1, max_length=24)
    confidence: str | None = Field(default=None, max_length=8)
    variable_ar: bool
    source_width: int = Field(gt=0, le=32_768)
    source_height: int = Field(gt=0, le=32_768)
    current_crop_top: int | None = Field(default=None, ge=0, le=32_768)
    current_crop_bottom: int | None = Field(default=None, ge=0, le=32_768)
    recommended_crop_top: int | None = Field(default=None, ge=0, le=32_768)
    recommended_crop_bottom: int | None = Field(default=None, ge=0, le=32_768)


class LetterboxApplyRequestV1(StrictDocument):
    media_file_id: int = Field(gt=0)
    subject_kind: Literal["movie", "episode"]
    subject_ids: tuple[int, ...] = Field(min_length=1, max_length=100)
    before: LetterboxSourceSnapshotV1
    crop_top: int = Field(ge=0, le=32_768)
    crop_bottom: int = Field(ge=0, le=32_768)
    source: Literal["api", "heal", "manual"] = "api"

    @model_validator(mode="after")
    def require_safe_crop(self) -> LetterboxApplyRequestV1:
        if any(subject_id <= 0 for subject_id in self.subject_ids) or len(set(self.subject_ids)) != len(
            self.subject_ids
        ):
            raise ValueError("letterbox mutation subjects must be unique positive identifiers")
        if self.before.variable_ar or self.before.status == "variable_unsafe":
            raise ValueError("variable-aspect media cannot be planned for automatic tag mutation")
        if self.crop_top + self.crop_bottom >= self.before.source_height:
            raise ValueError("crop removes the entire encoded frame")
        return self


class LetterboxRemoveRequestV1(StrictDocument):
    media_file_id: int = Field(gt=0)
    subject_kind: Literal["movie", "episode"]
    subject_ids: tuple[int, ...] = Field(min_length=1, max_length=100)
    before: LetterboxSourceSnapshotV1
    source: Literal["api", "heal", "manual"] = "api"

    @model_validator(mode="after")
    def require_unique_subjects(self) -> LetterboxRemoveRequestV1:
        if any(subject_id <= 0 for subject_id in self.subject_ids) or len(set(self.subject_ids)) != len(
            self.subject_ids
        ):
            raise ValueError("letterbox mutation subjects must be unique positive identifiers")
        return self


class LetterboxParentRequestV1(StrictDocument):
    operation: Literal["apply", "remove", "heal_apply", "heal_remove"]
    series_id: int | None = Field(default=None, gt=0)
    season_number: int | None = Field(default=None, ge=0, le=10_000)
    confidence_levels: tuple[str, ...] = Field(default=(), max_length=8)
    sealed_file_count: int = Field(ge=0, le=10_000)


class LetterboxProbeV1(StrictDocument):
    """Authoritative crop facts from one successful, fresh MKVToolNix probe."""

    source_signature: str = Field(min_length=1, max_length=200)
    container: str = Field(min_length=1, max_length=40)
    video_track_id: int = Field(ge=0)
    width: int = Field(gt=0, le=32_768)
    height: int = Field(gt=0, le=32_768)
    crop_present: bool
    crop_top: int | None = Field(default=None, ge=0, le=32_768)
    crop_bottom: int | None = Field(default=None, ge=0, le=32_768)
    crop_left: int | None = Field(default=None, ge=0, le=32_768)
    crop_right: int | None = Field(default=None, ge=0, le=32_768)

    @model_validator(mode="after")
    def require_complete_crop_facts(self) -> LetterboxProbeV1:
        values = (self.crop_top, self.crop_bottom, self.crop_left, self.crop_right)
        if self.crop_present != all(value is not None for value in values):
            raise ValueError("crop presence requires four explicit crop values")
        return self


class LetterboxMutationResultV1(MutationResultV1):
    before_probe: LetterboxProbeV1 | None = None
    actual_probe: LetterboxProbeV1 | None = None

    @model_validator(mode="after")
    def require_authoritative_probe_for_change(self) -> LetterboxMutationResultV1:
        if any(outcome.bytes_changed for outcome in self.target_outcomes) and (
            self.before_probe is None or self.actual_probe is None
        ):
            raise ValueError("published letterbox mutations require before and actual probes")
        return self
