"""Taste scorer — dual-mode CLIP poster evaluation.

Combines two scoring modes into a single ranking pipeline:

  **Negative filter (zero-shot):**
  Compares each candidate poster against pre-encoded negative text prompts
  ("floating heads", "actor grid", etc.).  Any candidate with cosine
  similarity above ``NEGATIVE_PROMPT_THRESHOLD`` to *any* prompt is rejected.

  **Taste match (positive):**
  Scores each survivor against the taste profile stored in a ``TasteStore``
  (CLIP embedding centroid + LAB colour histogram centroid) using the
  configured emb/colour weights (default 80/20).

The scorer depends on the abstract ``TasteStore`` interface, not any specific
backend — swap ``NumpyTasteStore`` for ``ChromaTasteStore`` when the
incremental approval feature is built without changing pipeline code.

Usage::

    from marquee.ml.taste_store import NumpyTasteStore

    store = NumpyTasteStore()
    scorer = TasteScorer(store=store)
    results = scorer.rank(candidates)  # → ScorerResult
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageFile

from marquee.config import settings
from marquee.ml.color_histogram import extract_color_histogram
from marquee.ml.embedding import CLIPImageEncoder
from marquee.ml.preprocessing import preprocess_image
from marquee.ml.taste_store import NumpyTasteStore, TasteStore

logger = logging.getLogger(__name__)

ImageFile.LOAD_TRUNCATED_IMAGES = True

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_MODELS_DIR = Path(__file__).parent / "models"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class CandidateScore:
    """Score breakdown for one poster candidate."""

    image_path: Path
    final_score: float = 0.0
    emb_similarity: float = 0.0           # cosine sim to taste centroid
    color_similarity: float = 0.0          # cosine sim to colour centroid
    neg_sim_max: float = 0.0               # highest cosine sim to any neg prompt
    rejected_by: str | None = None         # which negative prompt triggered rejection

    @property
    def accepted(self) -> bool:
        return self.rejected_by is None


@dataclass
class ScorerResult:
    """Full scoring output for a batch of candidates."""

    accepted: list[CandidateScore] = field(default_factory=list)
    rejected: list[CandidateScore] = field(default_factory=list)

    @property
    def ranked(self) -> list[CandidateScore]:
        """Accepted candidates sorted best-first by final_score."""
        return sorted(self.accepted, key=lambda s: s.final_score, reverse=True)

    @property
    def top(self) -> CandidateScore | None:
        """The highest-scoring accepted candidate, or None."""
        r = self.ranked
        return r[0] if r else None


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------


class TasteScorer:
    """Score and rank poster candidates using CLIP taste profile + negative prompts.

    Parameters (all optional — sensible defaults for standard layout):
        store: ``TasteStore`` implementation (default: ``NumpyTasteStore``).
        model_path: Path to CLIP ONNX model.
        prompts_path: Path to pre-encoded negative prompts .npy file.
        encoder: Pre-created ``CLIPImageEncoder`` (shared across instances).
        neg_threshold: Override for ``NEGATIVE_PROMPT_THRESHOLD``.
        emb_weight: Override for ``TASTE_EMB_WEIGHT``.
        color_weight: Override for ``TASTE_COLOR_WEIGHT``.
    """

    def __init__(
        self,
        *,
        store: TasteStore | None = None,
        model_path: str | Path | None = None,
        prompts_path: str | Path | None = None,
        encoder: CLIPImageEncoder | None = None,
        neg_threshold: float | None = None,
        emb_weight: float | None = None,
        color_weight: float | None = None,
    ):
        # --- Store ---
        self.store = store or NumpyTasteStore()

        # --- Encoder ---
        self._encoder = encoder
        self._model_path = Path(model_path) if model_path else None

        # --- Negative prompts ---
        self._prompts_path = (
            Path(prompts_path) if prompts_path
            else _MODELS_DIR / "negative_prompts.npy"
        )
        self._neg_embs: np.ndarray | None = None
        self._neg_prompts: list[str] = []
        self._neg_threshold = neg_threshold

        # --- Weights ---
        self._emb_weight = emb_weight
        self._color_weight = color_weight

    @property
    def encoder(self) -> CLIPImageEncoder:
        if self._encoder is None:
            self._encoder = CLIPImageEncoder(model_path=self._model_path)
        return self._encoder

    @property
    def neg_threshold(self) -> float:
        return (
            self._neg_threshold
            if self._neg_threshold is not None
            else settings.NEGATIVE_PROMPT_THRESHOLD
        )

    @property
    def emb_weight(self) -> float:
        return (
            self._emb_weight
            if self._emb_weight is not None
            else settings.TASTE_EMB_WEIGHT
        )

    @property
    def color_weight(self) -> float:
        return (
            self._color_weight
            if self._color_weight is not None
            else settings.TASTE_COLOR_WEIGHT
        )

    # ------------------------------------------------------------------
    # Negative prompt loading (lazy)
    # ------------------------------------------------------------------

    def _ensure_neg_loaded(self) -> None:
        """Load pre-encoded negative prompt vectors from .npy file."""
        if self._neg_embs is not None:
            return

        self._neg_prompts = list(settings.NEGATIVE_PROMPTS)
        threshold = self.neg_threshold

        if self._prompts_path.exists():
            self._neg_embs = np.load(str(self._prompts_path))
            logger.info(
                "Loaded %d pre-encoded negative prompts from %s (threshold=%.2f)",
                len(self._neg_embs), self._prompts_path, threshold,
            )
        else:
            logger.warning(
                "Negative prompts file not found: %s — negative filter disabled. "
                "Run `python -m marquee.ml.clip_export` to generate it.",
                self._prompts_path,
            )
            self._neg_embs = np.empty((0, 512), dtype=np.float32)

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def score_candidate(self, image_path: Path) -> CandidateScore:
        """Full scoring pipeline for a single poster image.

        Extracts CLIP embedding + colour histogram, runs the negative
        filter, then computes taste match score against the store.
        """
        score = CandidateScore(image_path=image_path)

        # --- Load image ---
        try:
            img = Image.open(image_path)
            img.load()
            img = img.convert("RGB")
        except Exception as exc:
            logger.warning("Could not open %s: %s", image_path.name, exc)
            score.rejected_by = f"image load error: {exc}"
            return score

        # --- CLIP embedding ---
        try:
            pixel_values = preprocess_image(img)
            emb = self.encoder.encode(pixel_values)
        except Exception as exc:
            logger.warning("Embedding failed for %s: %s", image_path.name, exc)
            score.rejected_by = f"embedding error: {exc}"
            return score

        # --- Colour histogram ---
        try:
            color = extract_color_histogram(image_path)
        except Exception as exc:
            logger.warning(
                "Colour histogram failed for %s: %s", image_path.name, exc
            )
            score.rejected_by = f"colour histogram error: {exc}"
            return score

        # --- Negative filter ---
        self._ensure_neg_loaded()
        neg_embs = self._neg_embs
        if neg_embs is not None and len(neg_embs) > 0:
            sims = np.dot(neg_embs, emb)  # (N,)
            max_idx = int(np.argmax(sims))
            max_sim = float(sims[max_idx])
            score.neg_sim_max = max_sim

            if max_sim > self.neg_threshold:
                prompt_text = (
                    self._neg_prompts[max_idx]
                    if max_idx < len(self._neg_prompts)
                    else "?"
                )
                score.rejected_by = (
                    f"negative prompt (sim={max_sim:.4f}): {prompt_text}"
                )
                return score

        # --- Taste match against store ---
        score.emb_similarity = float(
            np.clip(np.dot(self.store.centroid_emb, emb), -1.0, 1.0)
        )
        score.color_similarity = float(
            np.clip(np.dot(self.store.centroid_color, color), -1.0, 1.0)
        )
        score.final_score = (
            self.emb_weight * score.emb_similarity
            + self.color_weight * score.color_similarity
        )

        return score

    def rank(self, image_paths: list[Path]) -> ScorerResult:
        """Score all candidates and return ranked results.

        Args:
            image_paths: Paths to poster candidate images.

        Returns:
            ``ScorerResult`` with accepted and rejected candidates.
        """
        result = ScorerResult()

        for path in image_paths:
            s = self.score_candidate(path)
            if s.accepted:
                result.accepted.append(s)
            else:
                result.rejected.append(s)

        logger.info(
            "Scored %d candidates: %d accepted, %d rejected "
            "(store has %d posters)",
            len(image_paths), len(result.accepted), len(result.rejected),
            self.store.size,
        )
        if result.top:
            logger.info(
                "Top pick: %s (score=%.4f, emb=%.4f, color=%.4f)",
                result.top.image_path.name,
                result.top.final_score,
                result.top.emb_similarity,
                result.top.color_similarity,
            )

        return result

    # ------------------------------------------------------------------
    # Diagnostic helpers
    # ------------------------------------------------------------------

    def print_ranked_table(
        self,
        result: ScorerResult,
        top_n: int = 5,
    ) -> None:
        """Print a formatted ranked table of candidates."""
        ranked = result.ranked

        print()
        print("=" * 80)
        print(f"  SCORING RESULTS  (top {min(top_n, len(ranked))} highlighted)")
        print(f"  Store: {self.store.size} training posters")
        print("=" * 80)
        print(
            f"  {'Rank':>4s}  {'Score':>7s}  {'Visual':>7s}  "
            f"{'Color':>7s}  {'Neg':>7s}  Filename"
        )
        print("  " + "-" * 72)

        for rank, s in enumerate(ranked, 1):
            tag = "  ← TOP" if rank <= top_n else ""
            print(
                f"  {rank:>4d}  {s.final_score:>7.4f}  {s.emb_similarity:>7.4f}  "
                f"{s.color_similarity:>7.4f}  {s.neg_sim_max:>7.4f}  "
                f"{s.image_path.name}{tag}"
            )

        if result.rejected:
            print()
            print(f"  Rejected by negative filter ({len(result.rejected)}):")
            for s in result.rejected:
                print(f"    ✗ {s.image_path.name}: {s.rejected_by}")

        print("=" * 80 + "\n")
