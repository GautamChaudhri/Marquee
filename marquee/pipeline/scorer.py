"""Hand-weighted baseline ranking with an optional bounded residual correction."""

from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from marquee.core.pipeline_config import PipelineSettings, pipeline_settings
from marquee.ml.residual import ResidualArtifact, baseline_signature, score_residual_candidate
from marquee.pipeline.types import CandidateScore, FeatureVector

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ResidualRuntimeContext:
    """External identity that an active residual must match at scoring time."""

    library: str
    baseline_signature: str
    profile_checksum: str
    profile_generation: int
    artifact_id: int | None = None
    artifact_checksum: str | None = None


class ResidualCompatibilityError(RuntimeError):
    """A forced residual cannot safely score against the current runtime."""


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

    def __init__(
        self,
        baseline: WeightedScorer,
        artifact: ResidualArtifact,
        context: ResidualRuntimeContext,
    ):
        actual_signature = baseline_signature(baseline.config.scorer_weights)
        if context.baseline_signature != actual_signature:
            raise ResidualCompatibilityError("runtime baseline signature mismatch")
        compatible, reason = artifact.compatible(
            namespace=context.library,
            baseline=context.baseline_signature,
            profile_checksum=context.profile_checksum,
            profile_generation=context.profile_generation,
        )
        if not compatible:
            raise ResidualCompatibilityError(f"Residual artifact is dormant: {reason}")
        self.baseline = baseline
        self.artifact = artifact
        self.context = context

    def score(self, features: FeatureVector) -> tuple[float, dict[str, float]]:
        baseline_score, baseline_contributions = self.baseline.score(features)
        score = score_residual_candidate(
            baseline_probability=baseline_score,
            normalized_features=features.normalized,
            weights={
                name: float(weight)
                for name, weight in zip(
                    self.artifact.feature_names, self.artifact.weights, strict=True
                )
            },
            bias=self.artifact.bias,
            alpha=self.artifact.alpha,
            delta_max=self.artifact.delta_max,
        )
        contributions = {f"baseline:{name}": value for name, value in baseline_contributions.items()}
        contributions.update(
            {
                f"residual:{name}": self.artifact.alpha * value
                for name, value in score.contributions.items()
            }
        )
        contributions["baseline_score"] = baseline_score
        contributions["residual_delta"] = score.delta
        contributions["final_score"] = score.final_score
        return score.final_score, contributions


def select_scorer(
    config: PipelineSettings = pipeline_settings,
    artifact_path: Path | None = None,
    context: ResidualRuntimeContext | None = None,
) -> PosterScorer:
    """Resolve weighted baseline or a compatible bounded residual artifact."""
    mode = config.SCORER
    residual_path = artifact_path
    library = context.library if context is not None else "unknown"
    baseline = WeightedScorer(config)
    if mode == "weighted":
        logger.info("SCORER | weighted (forced) for library %s", library)
        return baseline
    try:
        if residual_path is None:
            raise FileNotFoundError("no residual artifact configured")
        if context is None:
            raise ResidualCompatibilityError("residual runtime context is required")
        if context.artifact_checksum is not None:
            checksum = hashlib.sha256(residual_path.read_bytes()).hexdigest()
            if checksum != context.artifact_checksum:
                raise ResidualCompatibilityError("residual artifact checksum mismatch")
        artifact = ResidualArtifact.load(residual_path)
        scorer = ResidualScorer(baseline, artifact, context)
    except FileNotFoundError:
        if mode == "residual":
            raise
        logger.info("SCORER | weighted (auto: no residual artifact) for library %s", library)
        return baseline
    except (ResidualCompatibilityError, RuntimeError) as exc:
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
