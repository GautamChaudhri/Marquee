"""Shared records passed between revised poster pipeline stages."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

Point = tuple[float, float]
BoundingBox = tuple[Point, Point, Point, Point]


@dataclass(frozen=True)
class OCRTextBox:
    text: str
    confidence: float
    bbox: BoundingBox
    area: float


@dataclass
class OCRCandidateResult:
    image_path: Path
    accepted: bool
    detected_text: str
    reason: str | None
    title_bbox: BoundingBox | None
    residual_boxes: list[OCRTextBox] = field(default_factory=list)


@dataclass
class FeatureVector:
    knn_sim: float
    aesthetic: float
    title_colorfulness: float
    text_residual: float
    resolution: float
    sharpness: float
    face_area: float
    provenance: float
    lang_match: float
    normalized: dict[str, float] = field(default_factory=dict)

    def raw_values(self) -> dict[str, float]:
        values = asdict(self)
        values.pop("normalized")
        return values


@dataclass
class CandidateScore:
    image_path: Path
    orig_filename: str
    features: FeatureVector | None = None
    final_score: float | None = None
    contributions: dict[str, float] = field(default_factory=dict)
    gate_decision: str | None = None
    gate_reason: str | None = None
    rank: int | None = None
    stage_reached: str = "fetch"
    rejection_reason: str | None = None
    original_download: bool | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "orig_filename": self.orig_filename,
            "image_path": str(self.image_path),
            "raw_features": self.features.raw_values() if self.features else None,
            "normalized_features": self.features.normalized if self.features else None,
            "contributions": self.contributions,
            "gate_decision": self.gate_decision,
            "gate_reason": self.gate_reason,
            "final_score": self.final_score,
            "rank": self.rank,
            "stage_reached": self.stage_reached,
            "rejection_reason": self.rejection_reason,
            "original_download": self.original_download,
        }
