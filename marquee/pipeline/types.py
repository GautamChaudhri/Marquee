"""Shared records passed between revised poster pipeline stages."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

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
    # Full structured OCR trace (see ocr_filter._process_image). JSON-native;
    # carried back from the worker so the pipeline can persist it for the
    # OCR-label tooling. None on the no-text/error early paths if unbuilt.
    diagnostics: dict | None = None


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
    # ── OCR diagnostics (durable copy of the text gate's read) ───────────
    # What PaddleOCR actually detected for this candidate, captured for every
    # poster that reached the OCR stage (accepted or rejected). Persisted into
    # the immutable per-run archive so it survives re-runs — the work-dir
    # pipeline.log is deleted and is per-title, not per-run. None until OCR
    # runs (rejected earlier, or an archive predating this field).
    ocr_detected_text: str | None = None
    # JSON-native serialized forms (bbox as a list of [x, y] points; each
    # residual box a dict of text/confidence/bbox/area/geometry_valid).
    ocr_title_bbox: list | None = None
    ocr_residual_boxes: list[dict] | None = None
    # Full structured OCR trace (every detected box across all passes, the
    # title match, per-box classification + significance reasoning, and the
    # accept/reject math). Only persisted on DEBUG runs — the OCR-label tooling
    # feeds it to an LLM to tune the text gate. None on non-DEBUG runs and on
    # archives predating this field. See ocr_filter._process_image.
    ocr_trace: dict | None = None
    # ── Stack layer (design: poster stacks) ──────────────────────────────
    # Ranked survivors of the same base design are grouped into a "stack".
    # stack_rank orders designs against each other; within a stack, members
    # are ordered by their individual score into positions 1,2,3 → labels
    # A,B,C. The auto-pick is stack_rank=1 / stack_pos=1 ("1A"). All None
    # until the stacker runs (or when STACK_ENABLED is off).
    stack_id: int | None = None
    stack_rank: int | None = None
    stack_pos: int | None = None
    stack_label: str | None = None
    stack_size: int | None = None
    stack_score: float | None = None
    # Transient similarity carrier (L2-normalized DINOv2/CLIP vector, or an
    # imagehash for the phash signal) handed from feature extraction to the
    # stacker. Never serialized — excluded from to_dict by omission.
    embedding: Any = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, object]:
        return {
            "orig_filename": self.orig_filename,
            "image_path": str(self.image_path),
            "raw_features": self.features.raw_values() if self.features else None,
            "extended_features": self.features.extended if self.features else None,
            "typicality_detail": (self.features.typicality_detail if self.features else None),
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
            "ocr_detected_text": self.ocr_detected_text,
            "ocr_title_bbox": self.ocr_title_bbox,
            "ocr_residual_boxes": self.ocr_residual_boxes,
            "ocr_trace": self.ocr_trace,
            "stack_id": self.stack_id,
            "stack_rank": self.stack_rank,
            "stack_pos": self.stack_pos,
            "stack_label": self.stack_label,
            "stack_size": self.stack_size,
            "stack_score": self.stack_score,
        }


def find_auto_pick_candidate(candidates: list[dict]) -> dict | None:
    """The run's auto-pick from a list of archived candidate dicts.

    The auto-pick is "1A" — the representative of the top stack
    (``stack_rank == 1 and stack_pos == 1``). With the robust top-K-mean stack
    score this can differ from the single globally highest-ranked poster, so we
    fall back to ``rank == 1`` when the run has no stack metadata (stacking off
    or an archive predating the stack layer).

    Operates on plain dicts (``CandidateScore.to_dict()`` / archive JSON) so it
    is shared by the results payload (`marquee.api.results`) and the persisted
    ``pipeline_runs.auto_pick_filename`` — both must agree on which poster won.
    """
    ranked = sorted(
        (c for c in candidates if c.get("rank") is not None),
        key=lambda c: c["rank"],
    )
    if not ranked:
        return None
    if ranked[0].get("stack_rank") is not None:
        rep = next(
            (c for c in ranked if c.get("stack_rank") == 1 and c.get("stack_pos") == 1),
            None,
        )
        if rep is not None:
            return rep
    return ranked[0]
