"""Environment-overridable configuration for the revised poster pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ML_DIR = _PROJECT_ROOT / "marquee" / "ml"
_MODELS_DIR = _ML_DIR / "models"
_DATA_DIR = _PROJECT_ROOT / "data"
_DATA_ML_DIR = _DATA_DIR / "ml"

_TMDB_SIZES = {"w92", "w154", "w185", "w342", "w500", "w780", "original"}


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
    # The TV profile trains on artwork already deployed in the library rather than
    # on recorded preference events (see marquee.core.tv_taste_scan), so its floor
    # counts poster files — every show.jpg and seasonNN.jpg found — not subjects.
    # Below this the k-NN corpus is too small to express taste and the build fails
    # loudly instead of publishing a profile that ranks on noise.
    TV_TASTE_MIN_POSTERS: int = 50
    PREFERRED_LANG: str = "en"

    # ── Taste map clustering ──────────────────────────────────────────
    # Minimum cluster size as fraction of profile size (e.g. 0.03 → ~15 at
    # 500 exemplars). Larger = fewer, larger clusters.
    TASTE_MAP_MIN_CLUSTER_SIZE_RATIO: float = 0.03
    # HDBSCAN cluster_selection_epsilon: max distance within a cluster on
    # the UMAP/PCA projection. 0 = let HDBSCAN choose. Smaller = tighter
    # clusters and more noise. 0.2-0.6 works well for UMAP coords.
    TASTE_MAP_CLUSTER_EPSILON: float = 0.4
    # HDBSCAN cluster_selection_method: 'eom' (excess of mass, fewer larger
    # clusters) or 'leaf' (more, smaller, more homogeneous clusters).
    TASTE_MAP_CLUSTER_METHOD: str = "leaf"

    CLIP_MODEL_PATH: Path = _MODELS_DIR / "clip-vit-b-32.onnx"
    AESTHETIC_MODEL_PATH: Path = _MODELS_DIR / "sa_0_4_vit_b_32_linear.pth"
    FACE_MODEL_PATH: Path = _MODELS_DIR / "scrfd_500m_bnkps.onnx"
    TASTE_PROFILE_PATH: Path = _DATA_ML_DIR / "taste_profile.clip-vit-b-32.npz"
    TASTE_PROFILE_TV_PATH: Path = _DATA_ML_DIR / "taste_profile.tv.clip-vit-b-32.npz"
    EMBEDDING_CACHE_DIR: Path = _PROJECT_ROOT / "data" / "cache" / "embeddings"

    # ── Extended features (recs 1-6) ─────────────────────────────────
    # DINOv2 second style opinion: "auto" enables it on GPU tiers (cuda,
    # openvino-gpu, coreml) and disables it on CPU tiers; "on"/"off" force.
    DINO_ENABLED: str = "auto"
    DINO_MODEL_PATH: Path = _MODELS_DIR / "dinov2-vits14.onnx"
    # Rec 5 master switch: quality-artifact metrics (blockiness + sensor
    # noise) and the person detector. Easy A/B: EXTRA_QUALITY_ENABLED=false.
    EXTRA_QUALITY_ENABLED: bool = True
    PERSON_MODEL_PATH: Path = _MODELS_DIR / "yolo11n.onnx"
    PERSON_CONFIDENCE_THRESHOLD: float = 0.40
    # Exemplar-calibrated normalization (rec 2): score each taste-band
    # feature by KDE typicality against the taste profile's per-feature
    # distributions instead of fixed higher-is-better ramps.
    CALIBRATION_ENABLED: bool = True
    # Bandwidth multiplier for the per-feature KDE. >1 = smoother/more
    # forgiving taste bands; <1 = sharper peaks around exemplar clusters.
    CALIBRATION_BANDWIDTH_SCALE: float = 1.0
    # Minimum non-NaN exemplar samples before a feature is calibrated;
    # below this the fixed normalization fallback is used.
    CALIBRATION_MIN_SAMPLES: int = 20
    # Zero-shot CLIP axes artifact (text-prompt directions). Built once via
    # `python -m marquee.ml.zeroshot`; skipped with a warning if absent.
    ZEROSHOT_AXES_PATH: Path = _DATA_ML_DIR / "zeroshot_axes.clip-vit-b-32.npz"
    # Residual artifacts are supplied only through immutable publication staging.
    SCORER: str = "auto"

    # ── Feedback loop (design 09) ─────────────────────────────────────
    FEEDBACK_GATE_ALERT_THRESHOLD: int = 5
    FEEDBACK_DEPLOY_DEFAULT: bool = True
    RESIDUAL_MIN_SUBJECTS: int = 25
    RESIDUAL_MIN_PAIRS: int = 200
    # A hated poster becomes a *negative exemplar* (taste-profile Channel 1) only
    # when the pipeline ranked it this high or better — i.e. a hard negative the
    # model was confidently wrong about. Easy negatives (ranked worse, or never
    # ranked) feed only the pairwise order, never the negative exemplar set.
    FEEDBACK_HARD_NEGATIVE_RANK_MAX: int = 10

    # ── Batch poster pipeline ─────────────────────────────────────────
    # Upper bound on movies admitted to one batch submission, so an accidental
    # "run the whole library" cannot queue unbounded work.
    PIPELINE_BATCH_MAX_MOVIES: int = 500
    # Roll stage-major poster groups out independently of the single-subject path.
    POSTER_GROUP_ENABLED: bool = False
    # "all_at_once" freezes the submitted selection into one stage-major group;
    # cancelling that group therefore cancels the entire selection.
    POSTER_GROUP_BATCH_MODE: Literal["chunked", "all_at_once"] = Field(
        default="chunked",
        description=(
            "Choose chunked groups or process the entire submitted movie/TV queue in one "
            "stage-major run. All-at-once is faster, but cancellation discards the whole run."
        ),
    )
    # Used only by chunked mode. All-at-once uses the frozen selection size.
    POSTER_GROUP_CHUNK_SIZE: int = 8
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
    # official_family CLIP-cosine ramp. Measured on Avengers/Interstellar/
    # Strange Darling (2026-06-11): same-art-family candidates sit at
    # 0.90+, official alternates 0.75-0.90, fan art 0.60-0.80.
    NORM_OFFICIAL_MIN: float = 0.60
    NORM_OFFICIAL_MAX: float = 0.95

    # Phase-0 scorer weights. The scorer renormalizes by the total of the
    # weights whose features are actually available, so the new features can
    # be toggled off (or their models absent) without breaking the [0,1] range.
    # 2026-06-11 rebalance from 3-movie labeled evidence: aesthetic
    # anti-discriminates (LAION head loves slick fan art) so it dropped;
    # provenance is nearly constant (TMDB poster votes are ~all zero) so it
    # dropped; official_family (CLIP cosine to the TMDB primary poster) was
    # the strongest single discriminator and dino/typicality both separated
    # kept-vs-flagged, so they absorbed the freed weight.
    WEIGHT_KNN_SIM: float = 0.30
    WEIGHT_AESTHETIC: float = 0.12
    WEIGHT_TITLE_COLORFULNESS: float = 0.15
    WEIGHT_FACE_AREA: float = 0.15
    WEIGHT_TEXT_RESIDUAL: float = 0.10
    WEIGHT_PROVENANCE: float = 0.02
    WEIGHT_SHARPNESS: float = 0.03
    WEIGHT_RESOLUTION: float = 0.0
    WEIGHT_LANG_MATCH: float = 0.0
    # New scorer features (recs 1-5). dino_knn only participates on GPU
    # tiers; taste_typicality is the mean KDE typicality over
    # TYPICALITY_FEATURES; quality_artifacts is monotonic (clean = 1).
    WEIGHT_DINO_KNN: float = 0.12
    WEIGHT_TASTE_TYPICALITY: float = 0.12
    WEIGHT_QUALITY_ARTIFACTS: float = 0.03
    # CLIP cosine to the movie's TMDB primary poster — "is this the official
    # key-art family?". Fan art diverges from the primary; official variants
    # (including clean title-only versions of a text-heavy primary) cluster
    # around it. None (weight redistributed) when the primary was never
    # embedded this run.
    WEIGHT_OFFICIAL_FAMILY: float = 0.12

    # Fine-grained features aggregated into taste_typicality. Each is scored
    # by closeness to the taste profile's own distribution of that feature.
    TYPICALITY_FEATURES: list[str] = [
        # palette / mood
        "darkness",
        "mean_saturation",
        "hue_entropy",
        "global_colorfulness",
        "contrast_rms",
        # composition
        "negative_space_frac",
        "edge_density",
        "visual_entropy",
        "symmetry",
        # typography geometry
        "title_height_frac",
        "title_y_center",
        "title_centeredness",
        # subject / faces / people
        "face_count",
        "largest_face_frac",
        "person_count",
        "person_area_frac",
        # CLIP zero-shot style axes
        "axis_illustrated",
        "axis_minimalist",
        "axis_vintage",
        # personalized quality band
        "aesthetic",
    ]

    # Quality-artifact raw blend saturation points (values at which each
    # component is considered fully bad).
    QUALITY_BLOCKINESS_SAT: float = 6.0
    QUALITY_NOISE_SAT: float = 12.0

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

    # ── Stacks ──────────────────────────────────────────────────────────
    # Instead of *deleting* near-duplicate posters, group same-design
    # variants (title moved, recolored, slightly cropped) into a "stack".
    # Every poster is still scored individually; stacks are ranked against
    # each other and variants ranked within their stack (1A, 1B, ...), so
    # the user compares distinct designs first, then drills into one.
    # When disabled, the legacy pHash-removal stage (DEDUP_PHASH_THRESHOLD)
    # runs instead and ranking stays flat.
    STACK_ENABLED: bool = True
    # Similarity signal used to decide "same design":
    #   dino  — DINOv2 cosine (structure/composition; best at "same artwork,
    #           different text/recolor"); falls back to CLIP when DINO is off.
    #   clip  — CLIP cosine (already cached; more semantic, may over-group).
    #   phash — perceptual-hash Hamming distance (whole-image; recolors and
    #           large title-moves can split a design).
    STACK_SIGNAL: str = "dino"
    # Cosine-similarity floor for two posters to share a stack (dino/clip).
    # Higher = tighter (more, smaller stacks). Tune against the library.
    STACK_SIM_THRESHOLD: float = 0.88
    # Hamming-distance ceiling for STACK_SIGNAL="phash" (out of 64 bits).
    STACK_PHASH_MAX_DISTANCE: int = 12
    # A stack's overall score is the mean of its top-K member scores
    # (robust/trimmed mean): rewards designs with several strong variants
    # without letting one weak variant drag the design down. K=1 == max.
    STACK_AGG_TOPK: int = 3

    # TMDB poster size for initial pipeline fetch. "original" downloads
    # uncapped resolution (1-10 MB per poster); smaller sizes save bandwidth
    # at the cost of slightly degraded OCR and face-detection accuracy.
    # "w500" (500 px wide, ~50-200 KB) is the default sweet spot.
    TMDB_POSTER_SIZE: str = "w500"

    # "auto" keeps the intended GPU-first OCR path: PaddleOCR uses GPU when
    # Paddle CUDA is available, otherwise CPU. Worker caps below keep that
    # from spawning enough GPU contexts to strand VRAM on 8GB cards.
    OCR_DEVICE: str = "auto"
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
    # not independent text. Only applies to SMALL boxes — a box past either
    # geometry threshold below is real text even when it hugs the title
    # (studio branding, directed-by lines, taglines).
    OCR_TITLE_PROXIMITY_PIXELS: float = 30.0
    # Geometry-based residual significance: recognition often garbles small
    # or stylized non-title text into 1-2 char fragments ("70MM" -> "mm",
    # "DIRECTED BY CHRISTOPHER NOLAN" -> "r") which the 4+ char word rule
    # then ignores. The DETECTION box still spans the visible text, so a
    # residual box covering >= this fraction of image area, or wider than
    # this fraction of image width, is significant regardless of what the
    # recognizer read.
    OCR_RESIDUAL_SIGNIFICANT_AREA_FRACTION: float = 0.005
    OCR_RESIDUAL_SIGNIFICANT_WIDTH_FRACTION: float = 0.40
    # The project target is "movie title and nothing else", so a poster must
    # have an identified title box to pass OCR.  Posters whose detected text
    # never fuzzy-matches the title are rejected with reason=no_title.
    # Disable only if a movie's title typography defeats OCR entirely AND the
    # batch-level fallback below is not enough.
    OCR_REQUIRE_TITLE: bool = True
    # Batch-level FALLBACK ONLY: textless / no-title posters are rejected per
    # image. When explicitly enabled, a movie with zero titled survivors can
    # rescue no_text/no_title posters so it still gets output. Default False
    # preserves the current target: title text is required and truly textless
    # posters stay rejected.
    OCR_ACCEPT_NO_TEXT: bool = False
    # Try a contrast-enhanced image pass before concluding no_text.
    OCR_ENHANCE_RETRY: bool = True
    # Targeted title recovery: if the normal passes would reject a poster as
    # no_text/no_title with no meaningful residuals, run a low-threshold
    # contrast/upscaled OCR pass and keep only boxes that explain the title.
    OCR_TITLE_RECOVERY_ENABLED: bool = True
    OCR_TITLE_RECOVERY_CONFIDENCE_THRESHOLD: float = 0.50

    # ── Text-gate mode presets (design 18) ───────────────────────────
    # "title_only" = current strict behaviour (require title, reject residual).
    # "textless"   = accept only textless posters (reject title + residual).
    # "custom"     = per-category allow/deny toggles below.
    OCR_TEXT_MODE: str = "title_only"
    OCR_ALLOW_TITLE: bool = True
    OCR_ALLOW_DIRECTOR: bool = False
    OCR_ALLOW_STUDIO: bool = False
    OCR_ALLOW_RATING: bool = False
    OCR_ALLOW_TAGLINE: bool = False
    OCR_ALLOW_BILLING: bool = False
    OCR_ALLOW_SEASON: bool = False

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
        if self.TASTE_PROFILE_PATH == _DATA_ML_DIR / "taste_profile.clip-vit-b-32.npz":
            self.TASTE_PROFILE_PATH = _DATA_ML_DIR / f"taste_profile.{self.AI_MODEL}.npz"
        if self.TASTE_PROFILE_TV_PATH == _DATA_ML_DIR / "taste_profile.tv.clip-vit-b-32.npz":
            self.TASTE_PROFILE_TV_PATH = _DATA_ML_DIR / f"taste_profile.tv.{self.AI_MODEL}.npz"
        if self.ZEROSHOT_AXES_PATH == _DATA_ML_DIR / "zeroshot_axes.clip-vit-b-32.npz":
            self.ZEROSHOT_AXES_PATH = _DATA_ML_DIR / f"zeroshot_axes.{self.AI_MODEL}.npz"
        for field_name in (
            "CLIP_MODEL_PATH",
            "AESTHETIC_MODEL_PATH",
            "FACE_MODEL_PATH",
            "TASTE_PROFILE_PATH",
            "TASTE_PROFILE_TV_PATH",
            "ZEROSHOT_AXES_PATH",
            "EMBEDDING_CACHE_DIR",
        ):
            path = getattr(self, field_name)
            if not path.is_absolute():
                setattr(self, field_name, (_PROJECT_ROOT / path).resolve())

        if self.K_NEIGHBORS < 1:
            raise ValueError("K_NEIGHBORS must be at least 1")
        if not 1 <= self.POSTER_GROUP_CHUNK_SIZE <= 16:
            raise ValueError("POSTER_GROUP_CHUNK_SIZE must be between 1 and 16")
        if self.KNN_WEIGHTING not in ("mean", "softmax"):
            raise ValueError("KNN_WEIGHTING must be 'mean' or 'softmax'")
        if self.KNN_SOFTMAX_TEMP <= 0:
            raise ValueError("KNN_SOFTMAX_TEMP must be positive")
        if self.TASTE_NEG_WEIGHT < 0:
            raise ValueError("TASTE_NEG_WEIGHT cannot be negative")
        if not 0.01 <= self.TASTE_MAP_MIN_CLUSTER_SIZE_RATIO <= 0.5:
            raise ValueError("TASTE_MAP_MIN_CLUSTER_SIZE_RATIO must be in [0.01, 0.5]")
        if self.TASTE_MAP_CLUSTER_EPSILON < 0:
            raise ValueError("TASTE_MAP_CLUSTER_EPSILON cannot be negative")
        if self.TASTE_MAP_CLUSTER_METHOD not in ("eom", "leaf"):
            raise ValueError("TASTE_MAP_CLUSTER_METHOD must be 'eom' or 'leaf'")
        if self.OCR_WORKERS < 0:
            raise ValueError("OCR_WORKERS cannot be negative")
        if self.OCR_MAX_RESIDUAL_BOXES < 0:
            raise ValueError("OCR_MAX_RESIDUAL_BOXES cannot be negative")
        if not 0 <= self.OCR_MAX_RESIDUAL_AREA_FRACTION <= 1:
            raise ValueError("OCR_MAX_RESIDUAL_AREA_FRACTION must be in [0, 1]")
        if not 0 <= self.OCR_TITLE_RECOVERY_CONFIDENCE_THRESHOLD <= 1:
            raise ValueError("OCR_TITLE_RECOVERY_CONFIDENCE_THRESHOLD must be in [0, 1]")
        if self.OCR_DEVICE not in ("auto", "cpu", "gpu"):
            raise ValueError("OCR_DEVICE must be 'auto', 'cpu', or 'gpu'")
        if self.TMDB_POSTER_SIZE not in _TMDB_SIZES:
            raise ValueError(f"TMDB_POSTER_SIZE must be one of {sorted(_TMDB_SIZES)}")
        if self.OCR_TEXT_MODE not in ("title_only", "textless", "custom"):
            raise ValueError("OCR_TEXT_MODE must be 'title_only', 'textless', or 'custom'")
        if self.DINO_ENABLED not in ("auto", "on", "off"):
            raise ValueError("DINO_ENABLED must be 'auto', 'on', or 'off'")
        if self.SCORER not in ("auto", "weighted", "residual"):
            raise ValueError("SCORER must be 'auto', 'weighted', or 'residual'")
        if self.RESIDUAL_MIN_SUBJECTS < 1:
            raise ValueError("RESIDUAL_MIN_SUBJECTS must be at least 1")
        if self.RESIDUAL_MIN_PAIRS < 1:
            raise ValueError("RESIDUAL_MIN_PAIRS must be at least 1")
        if self.FEEDBACK_HARD_NEGATIVE_RANK_MAX < 0:
            raise ValueError("FEEDBACK_HARD_NEGATIVE_RANK_MAX cannot be negative")
        if self.CALIBRATION_BANDWIDTH_SCALE <= 0:
            raise ValueError("CALIBRATION_BANDWIDTH_SCALE must be positive")
        if self.CALIBRATION_MIN_SAMPLES < 2:
            raise ValueError("CALIBRATION_MIN_SAMPLES must be at least 2")
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
            "dino_knn": self.WEIGHT_DINO_KNN,
            "taste_typicality": self.WEIGHT_TASTE_TYPICALITY,
            "quality_artifacts": self.WEIGHT_QUALITY_ARTIFACTS,
            "official_family": self.WEIGHT_OFFICIAL_FAMILY,
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
            "taste_map_min_cluster_size_ratio": self.TASTE_MAP_MIN_CLUSTER_SIZE_RATIO,
            "taste_map_cluster_epsilon": self.TASTE_MAP_CLUSTER_EPSILON,
            "taste_map_cluster_method": self.TASTE_MAP_CLUSTER_METHOD,
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
                "device": self.OCR_DEVICE,
                "workers": self.OCR_WORKERS,
                "detail_passes": self.OCR_DETAIL_PASSES,
                "max_residual_boxes": self.OCR_MAX_RESIDUAL_BOXES,
                "max_residual_area_fraction": self.OCR_MAX_RESIDUAL_AREA_FRACTION,
                "mode": self.OCR_TEXT_MODE,
                "require_title": self.OCR_REQUIRE_TITLE,
                "accept_no_text_fallback": self.OCR_ACCEPT_NO_TEXT,
                "allow_title": self.OCR_ALLOW_TITLE,
                "allow_director": self.OCR_ALLOW_DIRECTOR,
                "allow_studio": self.OCR_ALLOW_STUDIO,
                "allow_rating": self.OCR_ALLOW_RATING,
                "allow_tagline": self.OCR_ALLOW_TAGLINE,
                "allow_billing": self.OCR_ALLOW_BILLING,
                "confidence_threshold": self.OCR_CONFIDENCE_THRESHOLD,
                "strip_confidence_threshold": self.OCR_STRIP_CONFIDENCE_THRESHOLD,
                "bottom_confidence_threshold": self.OCR_BOTTOM_CONFIDENCE_THRESHOLD,
                "fuzzy_cutoff": self.OCR_FUZZY_CUTOFF,
                "title_proximity_pixels": self.OCR_TITLE_PROXIMITY_PIXELS,
                "residual_significant_area_fraction": (self.OCR_RESIDUAL_SIGNIFICANT_AREA_FRACTION),
                "residual_significant_width_fraction": (
                    self.OCR_RESIDUAL_SIGNIFICANT_WIDTH_FRACTION
                ),
                "enhance_retry": self.OCR_ENHANCE_RETRY,
                "title_recovery_enabled": self.OCR_TITLE_RECOVERY_ENABLED,
                "title_recovery_confidence_threshold": (
                    self.OCR_TITLE_RECOVERY_CONFIDENCE_THRESHOLD
                ),
            },
            "extended_features": {
                "dino_enabled": self.DINO_ENABLED,
                "extra_quality_enabled": self.EXTRA_QUALITY_ENABLED,
                "calibration_enabled": self.CALIBRATION_ENABLED,
                "calibration_bandwidth_scale": self.CALIBRATION_BANDWIDTH_SCALE,
                "typicality_features": self.TYPICALITY_FEATURES,
                "scorer": self.SCORER,
            },
            "dedup_phash_threshold": self.DEDUP_PHASH_THRESHOLD,
            "dedup_min_poster_width": self.DEDUP_MIN_POSTER_WIDTH,
            "stacks": {
                "enabled": self.STACK_ENABLED,
                "signal": self.STACK_SIGNAL,
                "sim_threshold": self.STACK_SIM_THRESHOLD,
                "phash_max_distance": self.STACK_PHASH_MAX_DISTANCE,
                "agg_topk": self.STACK_AGG_TOPK,
            },
        }


pipeline_settings = PipelineSettings()
