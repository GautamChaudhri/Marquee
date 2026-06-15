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
    # False for rotated-frame (vertical-textline) reads whose coordinates
    # cannot be trusted for size/position decisions.
    geometry_valid: bool = True


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
    # New top-level scorer features. None = not computed (model absent,
    # disabled by flag, or CPU tier for dino) — the scorer renormalizes
    # around missing features.
    dino_knn: float | None = None
    taste_typicality: float | None = None
    quality_artifacts: float | None = None
    # CLIP cosine to the movie's TMDB primary poster ("official key-art
    # family"). None when the primary was not embedded this run.
    official_family: float | None = None
    # False when OCR found no title box: title_colorfulness is then unknown
    # and normalization substitutes a neutral value instead of punishing the
    # poster as if it had a plain white title.
    title_found: bool = True
    # Fine-grained raw values behind taste_typicality and quality_artifacts
    # (darkness, saturation, title geometry, zero-shot axes, blockiness, ...).
    # Logged and serialized for cross-referencing and future head training.
    extended: dict[str, float] = field(default_factory=dict)
    # Per-feature KDE typicality detail (feature -> 0..1), for the logs/JSON.
    typicality_detail: dict[str, float] = field(default_factory=dict)
    normalized: dict[str, float] = field(default_factory=dict)

    def raw_values(self) -> dict[str, float]:
        """The scorer-level raw features (excludes bookkeeping fields)."""
        values = asdict(self)
        values.pop("normalized")
        values.pop("title_found")
        values.pop("extended")
        values.pop("typicality_detail")
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
    # When this candidate was removed as a SHA/pHash near-duplicate, the
    # filename of the survivor it collapsed into (used by the feedback
    # endpoint to remap a dedup-twin override onto its survivor).
    dedup_kept: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "orig_filename": self.orig_filename,
            "image_path": str(self.image_path),
            "raw_features": self.features.raw_values() if self.features else None,
            "extended_features": self.features.extended if self.features else None,
            "typicality_detail": (
                self.features.typicality_detail if self.features else None
            ),
            "normalized_features": self.features.normalized if self.features else None,
            "contributions": self.contributions,
            "gate_decision": self.gate_decision,
            "gate_reason": self.gate_reason,
            "final_score": self.final_score,
            "rank": self.rank,
            "stage_reached": self.stage_reached,
            "rejection_reason": self.rejection_reason,
            "original_download": self.original_download,
            "dedup_kept": self.dedup_kept,
        }
