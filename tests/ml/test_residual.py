"""Bounded residual ranking: evidence, evaluation, artifact, and scorer.

The residual is a bounded correction over the weighted baseline, never a replacement.
Covers which feedback counts as evidence, freezing only ordered eligible events, subject
splits that never leak between partitions, training that requires held-out improvement,
and a scorer that is identical to the baseline when the artifact is missing."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from marquee.config.pipeline import PipelineSettings
from marquee.ml.residual import (
    ResidualArtifact,
    ResidualEvaluation,
    ResidualPair,
    _baseline_logit_margin,
    _evaluate_partition,
    _pair_baseline_probabilities,
    baseline_signature,
    build_residual_pairs,
    freeze_residual_evidence,
    score_residual_candidate,
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


def test_residual_coordinator_freezes_only_ordered_eligible_events():
    eligible = _event()
    eligible.id = "event-1"
    revoked = _event()
    revoked.id = "event-2"
    revoked.revoked_event_id = "undo-1"

    frozen = freeze_residual_evidence([eligible, revoked])

    assert frozen.event_ids == ["event-1"]
    assert frozen.rows[0]["id"] == "event-1"
    assert frozen.digest != "event-1"
    assert len(frozen.checksum) == 64


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


# ── Training computes what scoring computes (M5d) ─────────────────────────
#
# Scoring clamps each candidate's correction, ``weights · x + bias``, at ``delta_max``. The
# trainer centres the features through the bias and trains through that same clamp, so a
# trained residual changes the order with feature values as they really are (most between
# 0.3 and 1.0), not only near zero.

DECIDING = "aesthetic"
FEATURE_NAMES = ["aesthetic", "knn_sim"]
ALPHA, DELTA_MAX = 0.5, 0.75


def _decided_pairs(
    *,
    owner: str = "feature",
    subjects: int = 60,
    candidates: int = 6,
    offset: float = 0.0,
    seed: int = 1,
    changed_subject: str | None = None,
) -> list[ResidualPair]:
    """One ranked event per subject over ``candidates`` candidates whose features lie in 0.4
    to 0.9 (plus ``offset``). The owner orders by ``aesthetic`` (``feature``), by the
    baseline's own score (``baseline``), or at random (``random``). The baseline's scores sit
    in 0.4 to 0.6 and do not know the owner's feature, so it agrees about half the time.

    ``changed_subject``'s feature values are moved (same order), to show which subjects the
    trainer reads."""
    rng = np.random.default_rng(seed)
    pairs: list[ResidualPair] = []
    for index in range(subjects):
        subject = f"movie:{index}"
        values = rng.uniform(0.4, 0.9, size=(candidates, len(FEATURE_NAMES))) + offset
        if subject == changed_subject:
            values = values * 0.5 + 0.45
        baseline = rng.uniform(0.4, 0.6, size=candidates)
        shuffle = rng.permutation(candidates)
        order = {
            "feature": list(np.argsort(-values[:, 0], kind="stable")),
            "baseline": list(np.argsort(-baseline, kind="stable")),
            "random": list(shuffle),
        }[owner]
        total = candidates * (candidates - 1) / 2
        for i, winner in enumerate(order):
            for loser in order[i + 1 :]:
                pairs.append(
                    ResidualPair(
                        subject=subject,
                        winner=dict(zip(FEATURE_NAMES, map(float, values[winner]), strict=True)),
                        loser=dict(zip(FEATURE_NAMES, map(float, values[loser]), strict=True)),
                        baseline_margin=float(baseline[winner] - baseline[loser]),
                        weight=1.0 / total,
                        confidence="strong",
                        winner_baseline_probability=float(baseline[winner]),
                        loser_baseline_probability=float(baseline[loser]),
                        event_id=f"event-{index}",
                    )
                )
    return pairs


def _train(pairs: list[ResidualPair], **kwargs):
    return train_residual(
        pairs,
        namespace="movies",
        baseline="baseline-v1",
        profile_checksum="a" * 64,
        evidence_revision="b" * 64,
        **kwargs,
    )


def _score(pair_side: dict[str, float], probability: float, weights: dict[str, float], bias: float):
    return score_residual_candidate(
        baseline_probability=probability,
        normalized_features=pair_side,
        weights=weights,
        bias=bias,
        alpha=ALPHA,
        delta_max=DELTA_MAX,
    )


def _artifact_weights(artifact: ResidualArtifact) -> dict[str, float]:
    return {
        name: float(weight)
        for name, weight in zip(artifact.feature_names, artifact.weights, strict=True)
    }


def _hand_accuracy(pairs: list[ResidualPair], weights: dict[str, float], bias: float) -> float:
    """Held-out accuracy computed straight from scoring, pair by pair."""
    right = total = 0.0
    for pair in pairs:
        winner_p, loser_p = _pair_baseline_probabilities(pair)
        winner = _score(pair.winner, winner_p, weights, bias)
        loser = _score(pair.loser, loser_p, weights, bias)
        right += pair.weight * float(winner.final_logit > loser.final_logit)
        total += pair.weight
    return right / total


def test_features_in_a_realistic_range_activate_and_an_offset_changes_only_the_bias():
    first, first_report = _train(_decided_pairs())
    shifted, shifted_report = _train(_decided_pairs(offset=0.35))

    assert first is not None and first_report["outcome"] == "activate"
    assert shifted is not None and shifted_report["outcome"] == "activate"
    assert first.evaluation.candidate_accuracy - first.evaluation.baseline_accuracy >= 0.02
    np.testing.assert_allclose(shifted.weights, first.weights, rtol=1e-9, atol=1e-9)
    for partition in ("validation", "test"):
        before = first_report["partitions"][partition]["metrics"]
        after = shifted_report["partitions"][partition]["metrics"]
        assert after["candidate_accuracy"] == pytest.approx(before["candidate_accuracy"])
        assert after["baseline_accuracy"] == pytest.approx(before["baseline_accuracy"])
    # Every feature moved by 0.35, so the centred values, and so the corrections, are equal.
    assert shifted.bias - first.bias == pytest.approx(-0.35 * float(first.weights.sum()))
    assert first.bias != 0.0


def test_the_trainers_pair_logit_is_scorings_difference_with_the_bias():
    from marquee.ml.residual import _pair_logits

    pairs = _decided_pairs(subjects=8)
    mean = np.asarray([0.3, 0.8])
    winners = np.asarray([[p.winner[n] for n in FEATURE_NAMES] for p in pairs]) - mean
    losers = np.asarray([[p.loser[n] for n in FEATURE_NAMES] for p in pairs]) - mean
    margins = np.asarray([_baseline_logit_margin(p) for p in pairs])
    clamped_sides: set[int] = set()
    for coefficients in ([0.2, -0.3], [2.5, 0.0], [-3.0, 1.0], [60.0, 40.0]):
        vector = np.asarray(coefficients, dtype=np.float64)
        logits, _winner_free, _loser_free = _pair_logits(
            winners, losers, margins, vector, alpha=ALPHA, delta_max=DELTA_MAX
        )
        weights = dict(zip(FEATURE_NAMES, coefficients, strict=True))
        bias = -float(vector @ mean)
        for pair, logit in zip(pairs, logits, strict=True):
            winner_p, loser_p = _pair_baseline_probabilities(pair)
            winner = _score(pair.winner, winner_p, weights, bias)
            loser = _score(pair.loser, loser_p, weights, bias)
            assert logit == pytest.approx(winner.final_logit - loser.final_logit, abs=1e-9)
            clamped_sides.add(
                int(abs(winner.raw_delta) >= DELTA_MAX) + int(abs(loser.raw_delta) >= DELTA_MAX)
            )
    assert clamped_sides == {0, 1, 2}  # neither side clamped, one side, and both


def test_the_held_out_check_scores_with_the_bias():
    pairs = _decided_pairs(subjects=10)
    weights = {"aesthetic": 1.5, "knn_sim": -0.4}
    for bias in (0.0, -0.6, 0.9):
        metrics = _evaluate_partition(pairs, weights, bias=bias, alpha=ALPHA, delta_max=DELTA_MAX)
        assert metrics["candidate_accuracy"] == pytest.approx(_hand_accuracy(pairs, weights, bias))

    artifact, report = _train(_decided_pairs())
    assert artifact is not None
    validation = set(report["partitions"]["validation"]["subject_ids"])
    held_out = [pair for pair in _decided_pairs() if pair.subject in validation]
    assert artifact.evaluation.candidate_accuracy == pytest.approx(
        _hand_accuracy(held_out, _artifact_weights(artifact), artifact.bias)
    )


def test_random_answers_and_answers_that_agree_with_the_baseline_do_not_activate():
    random_artifact, random_report = _train(_decided_pairs(owner="random", subjects=200))
    assert random_artifact is None
    assert random_report["reason"] == "no_held_out_improvement"

    agree_artifact, agree_report = _train(_decided_pairs(owner="baseline"))
    assert agree_artifact is None
    assert agree_report["reason"] == "no_held_out_improvement"
    assert agree_report["evaluation"]["baseline_accuracy"] == pytest.approx(1.0)


def test_every_candidate_of_a_trained_artifact_stays_within_the_bound():
    pairs = _decided_pairs()
    artifact, _report = _train(pairs)
    assert artifact is not None
    weights = _artifact_weights(artifact)
    for pair in pairs:
        winner_p, loser_p = _pair_baseline_probabilities(pair)
        for side, probability in ((pair.winner, winner_p), (pair.loser, loser_p)):
            scored = _score(side, probability, weights, artifact.bias)
            assert abs(scored.final_logit - scored.baseline_logit) <= ALPHA * DELTA_MAX + 1e-12


def test_the_feature_means_come_from_the_training_partition_only():
    pairs = _decided_pairs()
    artifact, report = _train(pairs)
    assert artifact is not None
    validation_subject = report["partitions"]["validation"]["subject_ids"][0]
    test_subject = report["partitions"]["test"]["subject_ids"][0]
    for subject in (validation_subject, test_subject):
        changed = _decided_pairs(changed_subject=subject)
        assert changed != pairs
        other, other_report = _train(changed)
        assert other is not None
        assert np.array_equal(other.weights, artifact.weights)
        assert other.bias == artifact.bias
        assert other_report["feature_means"] == report["feature_means"]


def test_the_feature_means_count_each_candidate_once():
    # Each subject's two top cards meet again (as a rank and its hated cards would): they now
    # sit in more pair rows than the others, and must still count once each.
    ranked = _decided_pairs()
    first_of_subject = {}
    for pair in ranked:
        first_of_subject.setdefault(pair.subject, pair)
    pairs = ranked + [pair for pair in first_of_subject.values() for _ in range(3)]
    _artifact, report = _train(pairs)
    train = set(report["partitions"]["train"]["subject_ids"])
    rows = [
        (pair.subject, tuple(side[name] for name in FEATURE_NAMES))
        for pair in pairs
        if pair.subject in train
        for side in (pair.winner, pair.loser)
    ]
    expected = np.mean([values for _subject, values in set(rows)], axis=0)
    by_rows = np.mean([values for _subject, values in rows], axis=0)
    assert not np.allclose(expected, by_rows)  # a mean over pair rows would differ
    assert [report["feature_means"][name] for name in FEATURE_NAMES] == pytest.approx(
        list(expected)
    )


def test_training_is_deterministic():
    first, _ = _train(_decided_pairs())
    second, _ = _train(_decided_pairs())
    assert first is not None and second is not None
    assert np.array_equal(first.weights, second.weights)
    assert first.bias == second.bias


def test_the_report_holds_the_bias_the_feature_means_and_the_clamped_share():
    import json

    for pairs in (_decided_pairs(), _decided_pairs(owner="baseline")):
        artifact, report = _train(pairs)
        assert isinstance(report["bias"], float)
        assert set(report["feature_means"]) == set(FEATURE_NAMES)
        if artifact is not None:
            assert report["bias"] == artifact.bias
            assert (
                artifact.partitions["sets"]["validation"]["metrics"]["clamped_share"]
                == (report["partitions"]["validation"]["metrics"]["clamped_share"])
            )
        for partition in ("validation", "test"):
            share = report["partitions"][partition]["metrics"]["clamped_share"]
            assert 0.0 <= share <= 1.0
        json.dumps(report, allow_nan=False)


def test_the_held_out_clamped_share_counts_candidates_at_or_beyond_the_clamp():
    pairs = _decided_pairs(subjects=6)
    weights = {"aesthetic": 3.0, "knn_sim": 0.0}
    bias = -1.3
    metrics = _evaluate_partition(pairs, weights, bias=bias, alpha=ALPHA, delta_max=DELTA_MAX)
    sides = [side for pair in pairs for side in (pair.winner, pair.loser)]
    expected = sum(abs(3.0 * side["aesthetic"] + bias) >= DELTA_MAX for side in sides) / len(sides)
    assert 0.0 < expected < 1.0
    assert metrics["clamped_share"] == pytest.approx(expected)


def test_an_artifact_with_a_bias_round_trips_and_scores_the_same(tmp_path):
    artifact, _report = _train(_decided_pairs())
    assert artifact is not None and artifact.bias != 0.0
    loaded = ResidualArtifact.load(artifact.save(tmp_path / "residual.npz"))
    assert loaded.bias == artifact.bias
    assert np.array_equal(loaded.weights, artifact.weights)

    config = PipelineSettings(SCORER="auto")
    baseline = WeightedScorer(config)
    signature = baseline_signature(config.scorer_weights)
    artifact.baseline_signature = loaded.baseline_signature = signature  # as a run would see it
    context = ResidualRuntimeContext(
        library="movies",
        baseline_signature=signature,
        profile_checksum="a" * 64,
        profile_generation=0,
    )
    for value in (0.4, 0.65, 0.9):
        features = _features(value)
        features.normalized["knn_sim"] = 1.3 - value
        assert ResidualScorer(baseline, loaded, context).score(features) == ResidualScorer(
            baseline, artifact, context
        ).score(features)
        _final, explanation = ResidualScorer(baseline, loaded, context).score(features)
        expected = _score(
            {name: features.normalized[name] for name in FEATURE_NAMES},
            explanation["baseline_score"],
            _artifact_weights(artifact),
            artifact.bias,
        )
        assert explanation["residual_delta"] == pytest.approx(expected.delta)
