"""JMC6J bounded residual evidence, evaluation, artifact, and scorer contracts."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from marquee.core.pipeline_config import PipelineSettings
from marquee.ml.residual import (
    ResidualArtifact,
    ResidualEvaluation,
    ResidualPair,
    baseline_signature,
    build_residual_pairs,
    subject_split,
    train_residual,
)
from marquee.pipeline.scorer import (
    ResidualRuntimeContext,
    ResidualScorer,
    WeightedScorer,
    select_scorer,
)
from marquee.pipeline.types import FeatureVector


def _features(value: float) -> FeatureVector:
    return FeatureVector(
        knn_sim=value,
        aesthetic=value,
        title_colorfulness=value,
        text_residual=value,
        resolution=value,
        sharpness=value,
        face_area=value,
        provenance=value,
        lang_match=value,
        normalized={"aesthetic": value},
    )


def _event(*, action: str = "approval", neutral: bool = False):
    return SimpleNamespace(
        action=action,
        subject_kind="movie",
        subject_reference="1",
        revoked_event_id=None,
        exposed_candidates=[
            {
                "candidate_id": "chosen",
                "baseline_rank": 1,
                "baseline_score": 0.7,
                "normalized_features": {"aesthetic": 0.9},
            },
            {
                "candidate_id": "alternative",
                "baseline_rank": 2,
                "baseline_score": 0.5,
                "normalized_features": {"aesthetic": 0.4},
            },
        ],
        training_context={
            "neutral_onboarding": neutral,
            "selected_candidate": "chosen",
            "order": ["chosen", "alternative"],
            "hated": [],
        },
    )


def test_natural_agreement_is_evidence_but_neutral_onboarding_is_not():
    pairs = build_residual_pairs([_event()])
    assert len(pairs) == 1
    assert pairs[0].baseline_margin == pytest.approx(0.2)
    assert pairs[0].confidence == "weak"
    assert build_residual_pairs([_event(neutral=True)]) == []


def test_subject_split_never_leaks_a_subject_between_partitions():
    subjects = [f"movie:{index}" for index in range(100)]
    first = subject_split(subjects, seed=7)
    second = subject_split(reversed(subjects), seed=7)
    assert first == second
    assert set(first.values()) == {"train", "validation", "test"}


def _pairs(*, baseline_margin: float) -> list[ResidualPair]:
    return [
        ResidualPair(
            subject=f"movie:{subject}",
            winner={"aesthetic": 1.0},
            loser={"aesthetic": 0.0},
            baseline_margin=baseline_margin,
            weight=0.1,
            confidence="strong",
        )
        for subject in range(30)
        for _pair in range(10)
    ]


def test_training_requires_held_out_improvement_and_preserves_no_change():
    artifact, report = train_residual(
        _pairs(baseline_margin=-0.05),
        namespace="movies",
        baseline="baseline-v1",
        profile_checksum="a" * 64,
        evidence_revision="b" * 64,
        seed=3,
    )
    assert artifact is not None
    assert report["outcome"] == "activate"
    assert artifact.evaluation.improvement > 0

    unchanged, report = train_residual(
        _pairs(baseline_margin=1.0),
        namespace="movies",
        baseline="baseline-v1",
        profile_checksum="a" * 64,
        evidence_revision="c" * 64,
        seed=3,
    )
    assert unchanged is None
    assert report["outcome"] == "no_change"
    assert report["reason"] == "no_held_out_improvement"


def test_residual_scorer_is_bounded_and_missing_artifact_is_baseline_identical(tmp_path):
    config = PipelineSettings(SCORER="auto")
    features = _features(0.7)
    baseline = WeightedScorer(config)
    expected = baseline.score(features)
    selected = select_scorer(config, artifact_path=tmp_path / "missing.npz")
    assert selected.score(features) == expected

    artifact = ResidualArtifact(
        namespace="movies",
        feature_names=["aesthetic"],
        weights=np.asarray([100.0]),
        bias=0.0,
        alpha=0.5,
        delta_max=0.2,
        baseline_signature=baseline_signature(config.scorer_weights),
        profile_checksum="a" * 64,
        evidence_revision="b" * 64,
        seed=0,
        evaluation=ResidualEvaluation(0.5, 0.6, 0.1, 5, 20, 0.05, 0.1),
        trained_at="2026-07-22T00:00:00+00:00",
    )
    scorer = ResidualScorer(
        baseline,
        artifact,
        ResidualRuntimeContext(
            library="movies",
            baseline_signature=baseline_signature(config.scorer_weights),
            profile_checksum="a" * 64,
            profile_generation=0,
        ),
    )
    final, explanation = scorer.score(features)
    baseline_score = expected[0]
    baseline_logit = np.log(baseline_score / (1 - baseline_score))
    final_logit = np.log(final / (1 - final))
    assert final_logit - baseline_logit == pytest.approx(0.1)
    assert explanation["residual_delta"] == pytest.approx(0.2)


def test_residual_artifact_round_trip_and_compatibility(tmp_path):
    artifact = ResidualArtifact(
        namespace="tv",
        feature_names=["aesthetic"],
        weights=np.asarray([0.25]),
        bias=0.0,
        alpha=0.5,
        delta_max=0.75,
        baseline_signature="baseline-v1",
        profile_checksum="a" * 64,
        evidence_revision="b" * 64,
        seed=11,
        evaluation=ResidualEvaluation(0.5, 0.6, 0.1, 5, 20, 0.05, 0.1),
        trained_at="2026-07-22T00:00:00+00:00",
    )
    path = artifact.save(tmp_path / "residual.npz")
    loaded = ResidualArtifact.load(path)
    assert loaded.namespace == "tv"
    assert loaded.weights.tolist() == [0.25]
    assert loaded.compatible(namespace="tv", baseline="baseline-v1") == (True, None)
    assert loaded.compatible(namespace="movies", baseline="baseline-v1")[0] is False
