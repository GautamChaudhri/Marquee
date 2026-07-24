"""Bounded residual preference evidence, training, evaluation, and artifacts."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from marquee.ml.artifact_codec import (
    decode_unicode_list,
    decode_unicode_scalar,
    load_npz_safe,
    save_npz_atomic,
    unicode_array,
    unicode_scalar,
)


@dataclass(frozen=True, slots=True)
class ResidualPair:
    subject: str
    winner: dict[str, float]
    loser: dict[str, float]
    baseline_margin: float
    weight: float
    confidence: str
    winner_baseline_probability: float | None = None
    loser_baseline_probability: float | None = None
    event_id: str = ""


@dataclass(frozen=True, slots=True)
class FrozenResidualEvidence:
    """The complete ordered, eligible feedback identity owned by the coordinator."""

    event_ids: list[str]
    rows: list[dict[str, Any]]
    digest: str
    checksum: str


def freeze_residual_evidence(events: Iterable[Any]) -> FrozenResidualEvidence:
    """Freeze only eligible, non-revoked, actually exposed feedback in input order."""
    rows: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(getattr(event, "id", None), str):
            continue
        if not build_residual_pairs([event]):
            continue
        rows.append(
            {
                "id": event.id,
                "action": event.action,
                "subject_kind": event.subject_kind,
                "subject_reference": event.subject_reference,
                "revoked_event_id": event.revoked_event_id,
                "exposed_candidates": event.exposed_candidates,
                "training_context": event.training_context,
            }
        )
    encoded = json.dumps(rows, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()
    event_ids = [row["id"] for row in rows]
    digest = hashlib.sha256(json.dumps(event_ids, separators=(",", ":")).encode()).hexdigest()
    return FrozenResidualEvidence(
        event_ids=event_ids,
        rows=rows,
        digest=digest,
        checksum=hashlib.sha256(encoded).hexdigest(),
    )


@dataclass(frozen=True, slots=True)
class ResidualEvaluation:
    baseline_accuracy: float
    candidate_accuracy: float
    improvement: float
    held_out_subjects: int
    held_out_pairs: int
    mean_abs_adjustment: float
    max_abs_adjustment: float


@dataclass(frozen=True, slots=True)
class ResidualCandidateScore:
    """Exact bounded residual transformation for one runtime candidate."""

    baseline_probability: float
    baseline_logit: float
    raw_delta: float
    delta: float
    final_logit: float
    final_score: float
    contributions: dict[str, float]


def score_residual_candidate(
    *,
    baseline_probability: float,
    normalized_features: dict[str, float],
    weights: dict[str, float],
    bias: float,
    alpha: float,
    delta_max: float,
) -> ResidualCandidateScore:
    """Apply the deployed residual math to exactly one candidate.

    The per-candidate delta clamp deliberately precedes alpha application.  Runtime
    scoring and held-out activation evaluation both call this function.
    """
    if not math.isfinite(baseline_probability):
        raise RuntimeError("Residual baseline probability is not finite")
    if not 0 <= alpha <= 1 or not 0 < delta_max <= 2:
        raise RuntimeError("Residual scoring bounds are invalid")
    if not math.isfinite(bias) or not all(math.isfinite(weight) for weight in weights.values()):
        raise RuntimeError("Residual scoring parameters are not finite")
    missing = [name for name in weights if name not in normalized_features]
    if missing:
        raise RuntimeError(f"Residual expects unavailable features: {missing}")
    values = {name: float(normalized_features[name]) for name in weights}
    if not all(math.isfinite(value) for value in values.values()):
        raise RuntimeError("Residual received non-finite features")
    epsilon = 1e-6
    bounded_baseline = max(epsilon, min(baseline_probability, 1.0 - epsilon))
    baseline_logit = math.log(bounded_baseline / (1.0 - bounded_baseline))
    contributions = {name: weight * values[name] for name, weight in weights.items()}
    raw_delta = sum(contributions.values()) + bias
    delta = max(-delta_max, min(raw_delta, delta_max))
    final_logit = baseline_logit + alpha * delta
    final_score = 1.0 / (1.0 + math.exp(-max(-30.0, min(final_logit, 30.0))))
    return ResidualCandidateScore(
        baseline_probability=baseline_probability,
        baseline_logit=baseline_logit,
        raw_delta=raw_delta,
        delta=delta,
        final_logit=final_logit,
        final_score=final_score,
        contributions=contributions,
    )


def _finite_features(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, float] = {}
    for name, raw in value.items():
        if not isinstance(name, str) or not isinstance(raw, (int, float)):
            return None
        number = float(raw)
        if not math.isfinite(number):
            return None
        result[name] = number
    return result or None


def build_residual_pairs(events: Iterable[Any]) -> list[ResidualPair]:
    """Build normalized subject-local pairs from actually exposed candidates."""
    pairs: list[ResidualPair] = []
    for event in events:
        context = event.training_context or {}
        if context.get("neutral_onboarding") is True or event.revoked_event_id is not None:
            continue
        exposed = {
            row.get("candidate_id"): row
            for row in (event.exposed_candidates or [])
            if isinstance(row, dict) and isinstance(row.get("candidate_id"), str)
        }
        usable = {
            identity: (row, _finite_features(row.get("normalized_features")))
            for identity, row in exposed.items()
        }
        usable = {identity: value for identity, value in usable.items() if value[1] is not None}
        subject = f"{event.subject_kind}:{event.subject_reference}"
        raw: list[tuple[str, str, float, str]] = []
        selected = context.get("selected_candidate")
        order = [identity for identity in context.get("order", []) if identity in usable]
        hated = [identity for identity in context.get("hated", []) if identity in usable]
        if event.action == "rank":
            for index, winner in enumerate(order):
                raw.extend((winner, loser, 1.0, "strong") for loser in order[index + 1 :])
            raw.extend(
                (winner, loser, 1.0, "explicit") for winner in order for loser in hated
            )
        elif event.action == "override" and selected in usable:
            alternatives = sorted(
                identity
                for identity in usable
                if identity != selected and usable[identity][0].get("baseline_rank") == 1
            )
            raw.extend((selected, loser, 1.0, "strong") for loser in alternatives[:1])
        elif event.action in {"approval", "selection"} and selected in usable:
            selected_rank = usable[selected][0].get("baseline_rank")
            alternatives = sorted(
                (
                    identity
                    for identity, (row, _features) in usable.items()
                    if identity != selected
                    and isinstance(row.get("baseline_rank"), int)
                    and isinstance(selected_rank, int)
                    and abs(row["baseline_rank"] - selected_rank) <= 3
                ),
                key=lambda identity: usable[identity][0]["baseline_rank"],
            )
            raw.extend((selected, loser, 0.25, "weak") for loser in alternatives[:3])
        if not raw:
            continue
        total = sum(weight for _winner, _loser, weight, _confidence in raw)
        for winner_id, loser_id, weight, confidence in raw:
            winner_row, winner = usable[winner_id]
            loser_row, loser = usable[loser_id]
            common = set(winner) & set(loser)
            if not common:
                continue
            winner_score = winner_row.get("baseline_score")
            loser_score = loser_row.get("baseline_score")
            baseline_margin = (
                float(winner_score) - float(loser_score)
                if isinstance(winner_score, (int, float))
                and isinstance(loser_score, (int, float))
                else 0.0
            )
            winner_probability = float(winner_score) if isinstance(winner_score, (int, float)) else None
            loser_probability = float(loser_score) if isinstance(loser_score, (int, float)) else None
            pairs.append(
                ResidualPair(
                    subject=subject,
                    winner={name: winner[name] for name in common},
                    loser={name: loser[name] for name in common},
                    baseline_margin=baseline_margin,
                    weight=weight / total,
                    confidence=confidence,
                    winner_baseline_probability=winner_probability,
                    loser_baseline_probability=loser_probability,
                    event_id=str(getattr(event, "id", "")),
                )
            )
    return pairs


def subject_split(subjects: Iterable[str], *, seed: int) -> dict[str, str]:
    """Assign whole subjects deterministically to train/validation/test."""
    ordered = sorted(
        set(subjects), key=lambda subject: (hashlib.sha256(f"{seed}:{subject}".encode()).digest(), subject)
    )
    if len(ordered) < 3:
        return dict.fromkeys(ordered, "train")
    validation_count = max(1, round(len(ordered) * 0.2))
    test_count = max(1, round(len(ordered) * 0.1))
    train_count = max(1, len(ordered) - validation_count - test_count)
    while train_count + validation_count + test_count > len(ordered):
        if validation_count > test_count and validation_count > 1:
            validation_count -= 1
        elif test_count > 1:
            test_count -= 1
        else:
            train_count -= 1
    return {
        subject: (
            "train"
            if index < train_count
            else "validation"
            if index < train_count + validation_count
            else "test"
        )
        for index, subject in enumerate(ordered)
    }


def baseline_signature(weights: dict[str, float]) -> str:
    payload = json.dumps(weights, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(slots=True)
class ResidualArtifact:
    namespace: str
    feature_names: list[str]
    weights: np.ndarray
    bias: float
    alpha: float
    delta_max: float
    baseline_signature: str
    profile_checksum: str
    evidence_revision: str
    seed: int
    evaluation: ResidualEvaluation
    trained_at: str
    profile_generation: int = 0
    partitions: dict[str, Any] = field(default_factory=dict)

    def delta(self, normalized: dict[str, float]) -> tuple[float, dict[str, float]]:
        missing = [name for name in self.feature_names if name not in normalized]
        if missing:
            raise RuntimeError(f"Residual expects unavailable features: {missing}")
        values = np.asarray([normalized[name] for name in self.feature_names], dtype=np.float64)
        if not np.isfinite(values).all():
            raise RuntimeError("Residual received non-finite features")
        contributions = {
            name: float(weight * value)
            for name, weight, value in zip(self.feature_names, self.weights, values, strict=True)
        }
        raw = float(values @ self.weights + self.bias)
        return max(-self.delta_max, min(raw, self.delta_max)), contributions

    def compatible(
        self,
        *,
        namespace: str,
        baseline: str,
        profile_checksum: str | None = None,
        profile_generation: int | None = None,
    ) -> tuple[bool, str | None]:
        if namespace != self.namespace:
            return False, "namespace mismatch"
        if baseline != self.baseline_signature:
            return False, "baseline configuration mismatch"
        if profile_checksum is not None and profile_checksum != self.profile_checksum:
            return False, "taste profile mismatch"
        if profile_generation is not None and profile_generation != self.profile_generation:
            return False, "taste profile generation mismatch"
        return True, None

    def save(self, path: Path) -> Path:
        save_npz_atomic(
            path,
            {
                "namespace": unicode_scalar(self.namespace),
                "feature_names": unicode_array(self.feature_names),
                "weights": np.asarray(self.weights, dtype=np.float64),
                "bias": np.float64(self.bias),
                "alpha": np.float64(self.alpha),
                "delta_max": np.float64(self.delta_max),
                "baseline_signature": unicode_scalar(self.baseline_signature),
                "profile_checksum": unicode_scalar(self.profile_checksum),
                "evidence_revision": unicode_scalar(self.evidence_revision),
                "seed": np.int64(self.seed),
                "evaluation_json": unicode_scalar(json.dumps(asdict(self.evaluation))),
                "trained_at": unicode_scalar(self.trained_at),
                "profile_generation": np.int64(self.profile_generation),
                "partitions_json": unicode_scalar(json.dumps(self.partitions, sort_keys=True)),
            },
        )
        return path

    @classmethod
    def load(cls, path: Path) -> ResidualArtifact:
        with load_npz_safe(path) as data:
            evaluation = ResidualEvaluation(**json.loads(decode_unicode_scalar(data["evaluation_json"])))
            names = decode_unicode_list(data["feature_names"])
            weights = np.asarray(data["weights"], dtype=np.float64)
            if weights.shape != (len(names),) or not np.isfinite(weights).all():
                raise RuntimeError("Residual artifact weights are invalid")
            alpha = float(data["alpha"])
            delta_max = float(data["delta_max"])
            if not (0 <= alpha <= 1 and 0 < delta_max <= 2):
                raise RuntimeError("Residual artifact bounds are invalid")
            if "profile_generation" not in data.files:
                raise RuntimeError("Residual artifact profile generation is missing")
            profile_generation = int(data["profile_generation"])
            if profile_generation < 0:
                raise RuntimeError("Residual artifact profile generation is invalid")
            return cls(
                namespace=decode_unicode_scalar(data["namespace"]),
                feature_names=names,
                weights=weights,
                bias=float(data["bias"]),
                alpha=alpha,
                delta_max=delta_max,
                baseline_signature=decode_unicode_scalar(data["baseline_signature"]),
                profile_checksum=decode_unicode_scalar(data["profile_checksum"]),
                evidence_revision=decode_unicode_scalar(data["evidence_revision"]),
                seed=int(data["seed"]),
                evaluation=evaluation,
                trained_at=decode_unicode_scalar(data["trained_at"]),
                profile_generation=profile_generation,
                partitions=(
                    json.loads(decode_unicode_scalar(data["partitions_json"]))
                    if "partitions_json" in data.files
                    else {}
                ),
            )


def train_residual(
    pairs: list[ResidualPair],
    *,
    namespace: str,
    baseline: str,
    profile_checksum: str,
    evidence_revision: str,
    profile_generation: int = 0,
    seed: int = 0,
    alpha: float = 0.5,
    delta_max: float = 0.75,
    min_subjects: int = 25,
    min_pairs: int = 200,
    min_improvement: float = 0.02,
    active_residual: ResidualArtifact | None = None,
) -> tuple[ResidualArtifact | None, dict[str, Any]]:
    """Fit a regularized baseline-offset pairwise residual and evaluate held-out subjects."""
    subjects = {pair.subject for pair in pairs}
    if len(subjects) < min_subjects or len(pairs) < min_pairs:
        return None, {"outcome": "no_change", "reason": "insufficient_evidence", "subjects": len(subjects), "pairs": len(pairs)}
    common = sorted(set.intersection(*(set(pair.winner) & set(pair.loser) for pair in pairs)))
    if not common:
        return None, {"outcome": "no_change", "reason": "no_common_features"}
    split = subject_split(subjects, seed=seed)
    train = [pair for pair in pairs if split[pair.subject] == "train"]
    validation = [pair for pair in pairs if split[pair.subject] == "validation"]
    test = [pair for pair in pairs if split[pair.subject] == "test"]
    if not train or not validation or not test:
        return None, {"outcome": "no_change", "reason": "insufficient_held_out_subjects"}
    diffs = np.asarray(
        [[pair.winner[name] - pair.loser[name] for name in common] for pair in train],
        dtype=np.float64,
    )
    margins = np.asarray([_baseline_logit_margin(pair) for pair in train], dtype=np.float64)
    sample_weights = np.asarray([pair.weight for pair in train], dtype=np.float64)
    coefficients = np.zeros(len(common), dtype=np.float64)
    weight_total = float(sample_weights.sum()) or 1.0
    for _iteration in range(2000):
        raw_delta = np.clip(diffs @ coefficients, -delta_max, delta_max)
        logits = margins + alpha * raw_delta
        probabilities = 1.0 / (1.0 + np.exp(-np.clip(logits, -30, 30)))
        gradient = -(((1.0 - probabilities) * sample_weights) @ diffs) / weight_total
        gradient += 0.05 * coefficients / len(train)
        step = 0.1 * gradient
        coefficients -= step
        if float(np.linalg.norm(step)) < 1e-8:
            break
    weights = {name: float(weight) for name, weight in zip(common, coefficients, strict=True)}
    validation_metrics = _evaluate_partition(
        validation, weights, alpha=alpha, delta_max=delta_max, active_residual=active_residual
    )
    test_metrics = _evaluate_partition(
        test, weights, alpha=alpha, delta_max=delta_max, active_residual=active_residual
    )
    evaluation = ResidualEvaluation(
        baseline_accuracy=validation_metrics["baseline_accuracy"],
        candidate_accuracy=validation_metrics["candidate_accuracy"],
        improvement=validation_metrics["improvement"],
        held_out_subjects=validation_metrics["subjects"],
        held_out_pairs=validation_metrics["pairs"],
        mean_abs_adjustment=validation_metrics["mean_abs_adjustment"],
        max_abs_adjustment=validation_metrics["max_abs_adjustment"],
    )
    partitions = _partition_manifest(train, validation, test)
    overlap_proof = partitions.pop("overlap_proof")
    partitions["validation"]["metrics"] = validation_metrics
    partitions["test"]["metrics"] = test_metrics
    report = {
        "outcome": "candidate",
        "evaluation": asdict(evaluation),
        "feature_names": common,
        "partitions": partitions,
        "partition_overlap_proof": overlap_proof,
    }
    if evaluation.improvement < min_improvement:
        return None, {**report, "outcome": "no_change", "reason": "no_held_out_improvement"}
    active_accuracy = validation_metrics.get("active_accuracy")
    if isinstance(active_accuracy, float) and evaluation.candidate_accuracy < active_accuracy:
        return None, {**report, "outcome": "no_change", "reason": "active_residual_regression"}
    return (
        ResidualArtifact(
            namespace=namespace,
            feature_names=common,
            weights=coefficients,
            bias=0.0,
            alpha=alpha,
            delta_max=delta_max,
            baseline_signature=baseline,
            profile_checksum=profile_checksum,
            evidence_revision=evidence_revision,
            seed=seed,
            evaluation=evaluation,
            trained_at=datetime.now(UTC).isoformat(),
            profile_generation=profile_generation,
            partitions={"sets": partitions, "overlap_proof": overlap_proof},
        ),
        {**report, "outcome": "activate"},
    )


def _pair_baseline_probabilities(pair: ResidualPair) -> tuple[float, float]:
    if pair.winner_baseline_probability is not None and pair.loser_baseline_probability is not None:
        return pair.winner_baseline_probability, pair.loser_baseline_probability
    return 0.5 + pair.baseline_margin / 2.0, 0.5 - pair.baseline_margin / 2.0


def _baseline_logit_margin(pair: ResidualPair) -> float:
    winner, loser = _pair_baseline_probabilities(pair)
    winner_score = score_residual_candidate(
        baseline_probability=winner,
        normalized_features={},
        weights={},
        bias=0.0,
        alpha=0.0,
        delta_max=1.0,
    )
    loser_score = score_residual_candidate(
        baseline_probability=loser,
        normalized_features={},
        weights={},
        bias=0.0,
        alpha=0.0,
        delta_max=1.0,
    )
    return winner_score.baseline_logit - loser_score.baseline_logit


def _evaluate_partition(
    pairs: list[ResidualPair],
    weights: dict[str, float],
    *,
    alpha: float,
    delta_max: float,
    active_residual: ResidualArtifact | None = None,
) -> dict[str, float | int]:
    weighted_baseline = 0.0
    weighted_candidate = 0.0
    weighted_active = 0.0
    total_weight = 0.0
    adjustments: list[float] = []
    for pair in pairs:
        winner_probability, loser_probability = _pair_baseline_probabilities(pair)
        winner = score_residual_candidate(
            baseline_probability=winner_probability,
            normalized_features={name: pair.winner[name] for name in weights},
            weights=weights,
            bias=0.0,
            alpha=alpha,
            delta_max=delta_max,
        )
        loser = score_residual_candidate(
            baseline_probability=loser_probability,
            normalized_features={name: pair.loser[name] for name in weights},
            weights=weights,
            bias=0.0,
            alpha=alpha,
            delta_max=delta_max,
        )
        total_weight += pair.weight
        weighted_baseline += pair.weight * float(winner.baseline_logit > loser.baseline_logit)
        weighted_candidate += pair.weight * float(winner.final_logit > loser.final_logit)
        if active_residual is not None:
            active_winner = score_residual_candidate(
                baseline_probability=winner_probability,
                normalized_features={name: pair.winner[name] for name in active_residual.feature_names},
                weights={
                    name: float(weight)
                    for name, weight in zip(
                        active_residual.feature_names, active_residual.weights, strict=True
                    )
                },
                bias=active_residual.bias,
                alpha=active_residual.alpha,
                delta_max=active_residual.delta_max,
            )
            active_loser = score_residual_candidate(
                baseline_probability=loser_probability,
                normalized_features={name: pair.loser[name] for name in active_residual.feature_names},
                weights={
                    name: float(weight)
                    for name, weight in zip(
                        active_residual.feature_names, active_residual.weights, strict=True
                    )
                },
                bias=active_residual.bias,
                alpha=active_residual.alpha,
                delta_max=active_residual.delta_max,
            )
            weighted_active += pair.weight * float(active_winner.final_logit > active_loser.final_logit)
        adjustments.extend((winner.delta * alpha, loser.delta * alpha))
    denominator = total_weight or 1.0
    baseline_accuracy = weighted_baseline / denominator
    candidate_accuracy = weighted_candidate / denominator
    result: dict[str, float | int] = {
        "subjects": len({pair.subject for pair in pairs}),
        "pairs": len(pairs),
        "baseline_accuracy": baseline_accuracy,
        "candidate_accuracy": candidate_accuracy,
        "improvement": candidate_accuracy - baseline_accuracy,
        "mean_abs_adjustment": float(np.mean(np.abs(adjustments))),
        "max_abs_adjustment": float(np.max(np.abs(adjustments))),
    }
    if active_residual is not None:
        result["active_accuracy"] = weighted_active / denominator
        result["candidate_vs_active"] = candidate_accuracy - float(result["active_accuracy"])
    return result


def _partition_manifest(
    train: list[ResidualPair], validation: list[ResidualPair], test: list[ResidualPair]
) -> dict[str, Any]:
    groups = {"train": train, "validation": validation, "test": test}
    manifest = {
        name: {
            "subject_ids": sorted({pair.subject for pair in pairs}),
            "event_ids": sorted({pair.event_id for pair in pairs if pair.event_id}),
            "pairs": len(pairs),
        }
        for name, pairs in groups.items()
    }
    subject_sets = [set(value["subject_ids"]) for value in manifest.values()]
    event_sets = [set(value["event_ids"]) for value in manifest.values()]
    manifest["overlap_proof"] = {
        "subject_overlap": not all(not left & right for index, left in enumerate(subject_sets) for right in subject_sets[index + 1 :]),
        "event_overlap": not all(not left & right for index, left in enumerate(event_sets) for right in event_sets[index + 1 :]),
    }
    if manifest["overlap_proof"]["subject_overlap"] or manifest["overlap_proof"]["event_overlap"]:
        raise RuntimeError("residual partitions overlap")
    return manifest
