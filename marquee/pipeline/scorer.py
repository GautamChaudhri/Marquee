"""Stage 6 pluggable poster ranking heads."""

from __future__ import annotations

from abc import ABC, abstractmethod

from marquee.core.pipeline_config import PipelineSettings, pipeline_settings
from marquee.pipeline.types import CandidateScore, FeatureVector


class PosterScorer(ABC):
    @abstractmethod
    def score(self, features: FeatureVector) -> tuple[float, dict[str, float]]:
        """Return a 0-1 score and normalized per-feature contributions."""


class WeightedScorer(PosterScorer):
    """Phase-0 positive weighted average over higher-is-better features."""

    def __init__(self, config: PipelineSettings = pipeline_settings):
        self.config = config

    def score(self, features: FeatureVector) -> tuple[float, dict[str, float]]:
        active_weights = {
            name: weight
            for name, weight in self.config.scorer_weights.items()
            if weight != 0
        }
        if any(weight < 0 for weight in active_weights.values()):
            raise ValueError("WeightedScorer requires positive weights")
        total_weight = sum(active_weights.values())
        if total_weight <= 0:
            raise ValueError("At least one positive scorer weight is required")

        contributions = {
            name: (
                features.normalized[name] * weight / total_weight
                if weight > 0
                else 0.0
            )
            for name, weight in self.config.scorer_weights.items()
        }
        final_score = max(0.0, min(sum(contributions.values()), 1.0))
        return final_score, contributions

    def rank(self, candidates: list[CandidateScore]) -> list[CandidateScore]:
        for candidate in candidates:
            if candidate.features is None:
                raise ValueError("Cannot rank a candidate without features")
            candidate.final_score, candidate.contributions = self.score(
                candidate.features
            )
        ranked = sorted(
            candidates,
            key=lambda candidate: candidate.final_score or 0.0,
            reverse=True,
        )
        for rank, candidate in enumerate(ranked, 1):
            candidate.rank = rank
            candidate.stage_reached = "ranked"
        return ranked
