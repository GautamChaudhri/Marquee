"""Stage 6 pluggable poster ranking heads.

Two interchangeable implementations behind ``PosterScorer``:

  - ``WeightedScorer`` — Phase-0 hand weights. Renormalizes over the
    features each candidate actually has, so optional features (dino on
    CPU tiers, quality pack toggled off) drop out cleanly without skewing
    the [0,1] score range.
  - ``LearnedScorer`` — Phase-1 logistic head trained on the user's own
    approvals/overrides (see ``marquee/ml/head_trainer.py``).

``select_scorer()`` resolves the SCORER config: "auto" prefers the learned
head when a valid trained artifact exists and logs which head is active —
the choice is part of every run's provenance.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from marquee.core.pipeline_config import PipelineSettings, pipeline_settings
from marquee.ml.learned_head import LogisticHead
from marquee.pipeline.types import CandidateScore, FeatureVector

logger = logging.getLogger(__name__)


class PosterScorer(ABC):
    name: str = "scorer"

    @abstractmethod
    def score(self, features: FeatureVector) -> tuple[float, dict[str, float]]:
        """Return a 0-1 score and per-feature contributions."""

    def rank(self, candidates: list[CandidateScore]) -> list[CandidateScore]:
        for candidate in candidates:
            if candidate.features is None:
                raise ValueError("Cannot rank a candidate without features")
            candidate.final_score, candidate.contributions = self.score(candidate.features)
        ranked = sorted(
            candidates,
            key=lambda candidate: candidate.final_score or 0.0,
            reverse=True,
        )
        for rank, candidate in enumerate(ranked, 1):
            candidate.rank = rank
            candidate.stage_reached = "ranked"
        return ranked


class WeightedScorer(PosterScorer):
    """Phase-0 positive weighted average over higher-is-better features."""

    name = "weighted"

    def __init__(self, config: PipelineSettings = pipeline_settings):
        self.config = config

    def score(self, features: FeatureVector) -> tuple[float, dict[str, float]]:
        weights = self.config.scorer_weights
        if any(weight < 0 for weight in weights.values()):
            raise ValueError("WeightedScorer requires positive weights")

        # A feature participates when it has a positive weight AND was
        # actually computed for this candidate (key present in normalized).
        # Absent optional features redistribute their weight to the rest.
        active = {
            name: weight
            for name, weight in weights.items()
            if weight > 0 and name in features.normalized
        }
        total_weight = sum(active.values())
        if total_weight <= 0:
            raise ValueError("At least one positive scorer weight is required")

        contributions = {
            name: (
                features.normalized[name] * weights[name] / total_weight if name in active else 0.0
            )
            for name in weights
        }
        final_score = max(0.0, min(sum(contributions.values()), 1.0))
        return final_score, contributions


class LearnedScorer(PosterScorer):
    """Phase-1 logistic head: score = P(user would pick this poster)."""

    name = "learned"

    def __init__(self, head: LogisticHead):
        self.head = head

    def score(self, features: FeatureVector) -> tuple[float, dict[str, float]]:
        return self.head.score(features.normalized)


def select_scorer(config: PipelineSettings = pipeline_settings) -> PosterScorer:
    """Resolve SCORER=auto|weighted|learned, logging the decision."""
    mode = config.SCORER
    if mode == "weighted":
        logger.info("SCORER | weighted (forced)")
        return WeightedScorer(config)
    if mode == "learned":
        head = LogisticHead.load()  # missing/mismatched artifact raises loudly
        logger.info(
            "SCORER | learned (forced) | n_samples=%d acc=%.3f trained_at=%s",
            head.n_samples,
            head.train_accuracy,
            head.trained_at,
        )
        return LearnedScorer(head)

    # auto: learned head when a valid artifact exists, else hand weights.
    try:
        head = LogisticHead.load()
    except FileNotFoundError:
        logger.info("SCORER | weighted (auto: no learned head artifact)")
        return WeightedScorer(config)
    except RuntimeError as exc:
        logger.warning("SCORER | weighted (auto: learned head rejected: %s)", exc)
        return WeightedScorer(config)
    logger.info(
        "SCORER | learned (auto) | n_samples=%d acc=%.3f trained_at=%s | features=%s",
        head.n_samples,
        head.train_accuracy,
        head.trained_at,
        head.feature_names,
    )
    return LearnedScorer(head)
