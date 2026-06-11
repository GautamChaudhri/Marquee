"""Environment-overridable configuration for the revised poster pipeline."""

from __future__ import annotations

from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ML_DIR = _PROJECT_ROOT / "marquee" / "ml"
_MODELS_DIR = _ML_DIR / "models"


class PipelineSettings(BaseSettings):
    """All tunable knobs for the GATE-then-RANK poster pipeline."""

    AI_MODEL: str = "clip-vit-b-32"
    # "auto" selects the best provider for the current host.  Friendly aliases
    # are accepted: cuda/nvidia, openvino/intel, openvino-cpu, coreml/apple,
    # cpu, tensorrt — as well as raw ONNX Runtime provider names.
    EXECUTION_PROVIDER: str = "auto"
    # CLIP images per ONNX run. 0 = auto by hardware tier (GPU 32, CPU 8).
    # Only takes effect with a dynamic-batch export; fixed-batch models loop.
    CLIP_BATCH_SIZE: int = 0
    # Lower is more permissive/multimodal/noisier; higher is smoother and
    # more conservative, with centroid-like blur returning at large values.
    K_NEIGHBORS: int = 10
    # How the k nearest exemplar similarities are combined into knn_sim:
    # "mean" = plain average; "softmax" = similarity-weighted average, which
    # favours the closest exemplars and sharpens multimodal taste clusters.
    KNN_WEIGHTING: str = "softmax"
    # Softmax temperature for KNN_WEIGHTING="softmax". Smaller = the nearest
    # exemplar dominates; larger = approaches the plain mean.
    KNN_SOFTMAX_TEMP: float = 0.1
    # Penalty multiplier when a candidate is closer to the negative (disliked)
    # exemplars than the positive ones: knn_sim -= w * max(0, neg - pos).
    # Only active when the taste profile contains negative exemplars.
    TASTE_NEG_WEIGHT: float = 1.0
    PREFERRED_LANG: str = "en"

    CLIP_MODEL_PATH: Path = _MODELS_DIR / "clip-vit-b-32.onnx"
    AESTHETIC_MODEL_PATH: Path = _MODELS_DIR / "sa_0_4_vit_b_32_linear.pth"
    FACE_MODEL_PATH: Path = _MODELS_DIR / "scrfd_500m_bnkps.onnx"
    TASTE_PROFILE_PATH: Path = _ML_DIR / "taste_profile.clip-vit-b-32.npz"
    EMBEDDING_CACHE_DIR: Path = _PROJECT_ROOT / "data" / "cache" / "embeddings"

    # Fixed Phase-0 normalization ranges.
    NORM_KNN_MIN: float = 0.4
    NORM_KNN_MAX: float = 0.9
    NORM_AESTHETIC_MAX: float = 10.0
    NORM_TITLE_COLORFULNESS_MAX: float = 60.0
    # Normalized title_colorfulness when OCR found no title box at all
    # (stylized/unreadable typography). Neutral instead of 0 so posters whose
    # title the OCR cannot read are not punished as if they had a white title.
    NORM_TITLE_COLORFULNESS_NEUTRAL: float = 0.5
    NORM_RESOLUTION_MAX_MP: float = 6.0
    NORM_SHARPNESS_MAX: float = 2000.0

    # Phase-0 scorer weights.
    WEIGHT_KNN_SIM: float = 0.30
    WEIGHT_AESTHETIC: float = 0.20
    WEIGHT_TITLE_COLORFULNESS: float = 0.15
    WEIGHT_FACE_AREA: float = 0.15
    WEIGHT_TEXT_RESIDUAL: float = 0.10
    WEIGHT_PROVENANCE: float = 0.07
    WEIGHT_SHARPNESS: float = 0.03
    WEIGHT_RESOLUTION: float = 0.0
    WEIGHT_LANG_MATCH: float = 0.0

    # Hard gate thresholds.
    GATE_MIN_WIDTH: int = 500
    GATE_MIN_AESTHETIC: float = 4.5
    GATE_MIN_KNN_SIM: float = 0.45
    GATE_FAN_JUNK_ENABLED: bool = False
    GATE_FAN_JUNK_MAX_AESTHETIC: float = 5.0
    GATE_FAN_JUNK_MAX_PROVENANCE: float = 0.55
    GATE_FAN_JUNK_MAX_RESOLUTION_MP: float = 1.0
    # Aesthetic rescue: if knn_sim >= GATE_AESTHETIC_RESCUE_KNN the taste profile
    # validates the poster, so relax the aesthetic floor to GATE_MIN_AESTHETIC_RESCUED.
    # Stylized/graphic-design posters score lower on photographic-quality models but
    # are on-brand — the taste profile is the higher-quality signal here.
    GATE_AESTHETIC_RESCUE_KNN: float = 0.55
    GATE_MIN_AESTHETIC_RESCUED: float = 2.0

    PROV_PRIOR_MEAN: float = 6.5
    PROV_CONFIDENCE: float = 25.0
    RESIDUAL_COUNT_SAT: int = 5
    RESIDUAL_WEIGHT_COUNT: float = 0.5
    RESIDUAL_WEIGHT_AREA: float = 0.5

    DEDUP_PHASH_THRESHOLD: int = 6
    DEDUP_MIN_POSTER_WIDTH: int = 500

    # 0 = auto-size from the hardware profile (cpu_count based, capped).
    OCR_WORKERS: int = 0
    # Run the extra top-strip and 2x-upscaled bottom-strip OCR passes.
    # Disable on very weak CPUs (e.g. Intel N150) to cut OCR time ~60% at the
    # cost of occasionally missing faint credit-block text.
    OCR_DETAIL_PASSES: bool = True
    # Text-heavy gate thresholds. A poster is rejected when it has MORE
    # significant residual boxes than OCR_MAX_RESIDUAL_BOXES OR their total
    # area exceeds OCR_MAX_RESIDUAL_AREA_FRACTION of the image.
    # Default 0 = STRICT title-only: any significant non-title text rejects
    # the poster (the current project target). Raise to 1-2 later to tolerate
    # taglines as a text_residual rank penalty instead of a rejection.
    OCR_MAX_RESIDUAL_BOXES: int = 0
    OCR_MAX_RESIDUAL_AREA_FRACTION: float = 0.04
    OCR_CONFIDENCE_THRESHOLD: float = 0.75
    OCR_STRIP_CONFIDENCE_THRESHOLD: float = 0.65
    OCR_BOTTOM_CONFIDENCE_THRESHOLD: float = 0.50
    # Fuzzy-match cutoff for classifying an OCR word as a title token.
    # Lower = more lenient (fewer garbled partial-reads become residual).
    OCR_FUZZY_CUTOFF: float = 0.60
    # Residual boxes whose center falls within this many pixels of the
    # identified title bbox are treated as OCR fragments of the title,
    # not independent text.
    OCR_TITLE_PROXIMITY_PIXELS: float = 30.0
    # If set, no_text results are accepted rather than rejected.  Addresses
    # the large class of posters with stylized title fonts that PP-OCRv5_mobile
    # cannot read.  The contrast-enhanced retry runs first; this flag is the
    # fallback for when even the retry finds nothing.
    OCR_ACCEPT_NO_TEXT: bool = True
    # Try a contrast-enhanced image pass before concluding no_text.
    OCR_ENHANCE_RETRY: bool = True

    FACE_CONFIDENCE_THRESHOLD: float = 0.5
    FACE_NMS_THRESHOLD: float = 0.4

    model_config = SettingsConfigDict(
        env_file=_PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_file_override=False,
        env_prefix="",
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_pipeline_settings(self) -> PipelineSettings:
        # Keep model-specific artifacts in sync with AI_MODEL unless the user
        # pinned an explicit path (e.g. AI_MODEL=clip-vit-b-32-int8 picks up
        # clip-vit-b-32-int8.onnx and taste_profile.clip-vit-b-32-int8.npz).
        if self.CLIP_MODEL_PATH == _MODELS_DIR / "clip-vit-b-32.onnx":
            self.CLIP_MODEL_PATH = _MODELS_DIR / f"{self.AI_MODEL}.onnx"
        if self.TASTE_PROFILE_PATH == _ML_DIR / "taste_profile.clip-vit-b-32.npz":
            self.TASTE_PROFILE_PATH = _ML_DIR / f"taste_profile.{self.AI_MODEL}.npz"
        for field_name in (
            "CLIP_MODEL_PATH",
            "AESTHETIC_MODEL_PATH",
            "FACE_MODEL_PATH",
            "TASTE_PROFILE_PATH",
            "EMBEDDING_CACHE_DIR",
        ):
            path = getattr(self, field_name)
            if not path.is_absolute():
                setattr(self, field_name, (_PROJECT_ROOT / path).resolve())
        if self.K_NEIGHBORS < 1:
            raise ValueError("K_NEIGHBORS must be at least 1")
        if self.KNN_WEIGHTING not in ("mean", "softmax"):
            raise ValueError("KNN_WEIGHTING must be 'mean' or 'softmax'")
        if self.KNN_SOFTMAX_TEMP <= 0:
            raise ValueError("KNN_SOFTMAX_TEMP must be positive")
        if self.TASTE_NEG_WEIGHT < 0:
            raise ValueError("TASTE_NEG_WEIGHT cannot be negative")
        if self.OCR_MAX_RESIDUAL_BOXES < 0:
            raise ValueError("OCR_MAX_RESIDUAL_BOXES cannot be negative")
        if not 0 <= self.OCR_MAX_RESIDUAL_AREA_FRACTION <= 1:
            raise ValueError("OCR_MAX_RESIDUAL_AREA_FRACTION must be in [0, 1]")
        if self.RESIDUAL_COUNT_SAT < 1:
            raise ValueError("RESIDUAL_COUNT_SAT must be at least 1")
        if self.RESIDUAL_WEIGHT_COUNT < 0 or self.RESIDUAL_WEIGHT_AREA < 0:
            raise ValueError("Residual blend weights cannot be negative")
        if any(weight < 0 for weight in self.scorer_weights.values()):
            raise ValueError("All scorer weights must be non-negative")
        if sum(self.scorer_weights.values()) <= 0:
            raise ValueError("At least one scorer weight must be positive")
        positive_ranges = {
            "NORM_AESTHETIC_MAX": self.NORM_AESTHETIC_MAX,
            "NORM_TITLE_COLORFULNESS_MAX": self.NORM_TITLE_COLORFULNESS_MAX,
            "NORM_RESOLUTION_MAX_MP": self.NORM_RESOLUTION_MAX_MP,
            "NORM_SHARPNESS_MAX": self.NORM_SHARPNESS_MAX,
        }
        for name, value in positive_ranges.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.NORM_KNN_MAX <= self.NORM_KNN_MIN:
            raise ValueError("NORM_KNN_MAX must be greater than NORM_KNN_MIN")
        return self

    @property
    def scorer_weights(self) -> dict[str, float]:
        return {
            "knn_sim": self.WEIGHT_KNN_SIM,
            "aesthetic": self.WEIGHT_AESTHETIC,
            "title_colorfulness": self.WEIGHT_TITLE_COLORFULNESS,
            "face_area": self.WEIGHT_FACE_AREA,
            "text_residual": self.WEIGHT_TEXT_RESIDUAL,
            "provenance": self.WEIGHT_PROVENANCE,
            "sharpness": self.WEIGHT_SHARPNESS,
            "resolution": self.WEIGHT_RESOLUTION,
            "lang_match": self.WEIGHT_LANG_MATCH,
        }

    def snapshot(self) -> dict[str, object]:
        """Return the active knobs needed to reproduce a pipeline run."""
        return {
            "ai_model": self.AI_MODEL,
            "execution_provider": self.EXECUTION_PROVIDER,
            "clip_batch_size": self.CLIP_BATCH_SIZE,
            "k_neighbors": self.K_NEIGHBORS,
            "knn_weighting": self.KNN_WEIGHTING,
            "knn_softmax_temp": self.KNN_SOFTMAX_TEMP,
            "taste_neg_weight": self.TASTE_NEG_WEIGHT,
            "preferred_lang": self.PREFERRED_LANG,
            "weights": self.scorer_weights,
            "gates": {
                "min_width": self.GATE_MIN_WIDTH,
                "min_aesthetic": self.GATE_MIN_AESTHETIC,
                "min_knn_sim": self.GATE_MIN_KNN_SIM,
                "fan_junk_enabled": self.GATE_FAN_JUNK_ENABLED,
                "fan_junk_max_aesthetic": self.GATE_FAN_JUNK_MAX_AESTHETIC,
                "fan_junk_max_provenance": self.GATE_FAN_JUNK_MAX_PROVENANCE,
                "fan_junk_max_resolution_mp": self.GATE_FAN_JUNK_MAX_RESOLUTION_MP,
            },
            "ocr": {
                "workers": self.OCR_WORKERS,
                "detail_passes": self.OCR_DETAIL_PASSES,
                "max_residual_boxes": self.OCR_MAX_RESIDUAL_BOXES,
                "max_residual_area_fraction": self.OCR_MAX_RESIDUAL_AREA_FRACTION,
            },
            "dedup_phash_threshold": self.DEDUP_PHASH_THRESHOLD,
            "dedup_min_poster_width": self.DEDUP_MIN_POSTER_WIDTH,
        }


pipeline_settings = PipelineSettings()
