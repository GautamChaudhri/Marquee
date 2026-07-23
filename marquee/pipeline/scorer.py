"""Hand-weighted baseline ranking with an optional bounded residual correction."""

from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from pathlib import Path

from marquee.core.pipeline_config import PipelineSettings, pipeline_settings
from marquee.ml.namespaces import TasteNamespace
from marquee.ml.residual import ResidualArtifact, baseline_signature
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


class ResidualScorer(PosterScorer):
    """Apply a bounded logit correction without bypassing the weighted baseline."""

    name = "residual"

    def __init__(self, baseline: WeightedScorer, artifact: ResidualArtifact):
        compatible, reason = artifact.compatible(
            namespace=artifact.namespace,
            baseline=baseline_signature(baseline.config.scorer_weights),
        )
        if not compatible:
            raise RuntimeError(f"Residual artifact is dormant: {reason}")
        self.baseline = baseline
        self.artifact = artifact

    def score(self, features: FeatureVector) -> tuple[float, dict[str, float]]:
        baseline_score, baseline_contributions = self.baseline.score(features)
        epsilon = 1e-6
        bounded = max(epsilon, min(baseline_score, 1.0 - epsilon))
        baseline_logit = math.log(bounded / (1.0 - bounded))
        delta, residual_contributions = self.artifact.delta(features.normalized)
        final_logit = baseline_logit + self.artifact.alpha * delta
        final_score = 1.0 / (1.0 + math.exp(-max(-30.0, min(final_logit, 30.0))))
        contributions = {f"baseline:{name}": value for name, value in baseline_contributions.items()}
        contributions.update(
            {f"residual:{name}": self.artifact.alpha * value for name, value in residual_contributions.items()}
        )
        contributions["baseline_score"] = baseline_score
        contributions["residual_delta"] = delta
        contributions["final_score"] = final_score
        return final_score, contributions


def select_scorer(
    config: PipelineSettings = pipeline_settings,
    namespace: TasteNamespace | None = None,
    artifact_path: Path | None = None,
) -> PosterScorer:
    """Resolve weighted baseline or a compatible bounded residual artifact."""
    mode = config.SCORER
    residual_path = artifact_path
    library = namespace.library if namespace is not None else "movies"
    baseline = WeightedScorer(config)
    if mode == "weighted":
        logger.info("SCORER | weighted (forced) for library %s", library)
        return baseline
    try:
        if residual_path is None:
            raise FileNotFoundError("no residual artifact configured")
        artifact = ResidualArtifact.load(residual_path)
        scorer = ResidualScorer(baseline, artifact)
    except FileNotFoundError:
        if mode == "residual":
            raise
        logger.info("SCORER | weighted (auto: no residual artifact) for library %s", library)
        return baseline
    except RuntimeError as exc:
        if mode == "residual":
            raise
        logger.warning("SCORER | weighted (auto: residual dormant: %s) for library %s", exc, library)
        return baseline
    logger.info(
        "SCORER | residual (%s) for library %s | revision=%s features=%s",
        mode,
        library,
        artifact.evidence_revision,
        artifact.feature_names,
    )
    return scorer
