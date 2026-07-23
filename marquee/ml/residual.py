"""Bounded residual preference evidence, training, evaluation, and artifacts."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass
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


@dataclass(frozen=True, slots=True)
class ResidualEvaluation:
    baseline_accuracy: float
    candidate_accuracy: float
    improvement: float
    held_out_subjects: int
    held_out_pairs: int
    mean_abs_adjustment: float
    max_abs_adjustment: float


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
            pairs.append(
                ResidualPair(
                    subject=subject,
                    winner={name: winner[name] for name in common},
                    loser={name: loser[name] for name in common},
                    baseline_margin=baseline_margin,
                    weight=weight / total,
                    confidence=confidence,
                )
            )
    return pairs


def subject_split(subjects: Iterable[str], *, seed: int) -> dict[str, str]:
    """Assign whole subjects deterministically to train/validation/test."""
    result = {}
    for subject in sorted(set(subjects)):
        digest = hashlib.sha256(f"{seed}:{subject}".encode()).digest()
        bucket = int.from_bytes(digest[:8], "big") % 10
        result[subject] = "train" if bucket < 7 else ("validation" if bucket < 9 else "test")
    return result


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
        self, *, namespace: str, baseline: str, profile_checksum: str | None = None
    ) -> tuple[bool, str | None]:
        if namespace != self.namespace:
            return False, "namespace mismatch"
        if baseline != self.baseline_signature:
            return False, "baseline configuration mismatch"
        if profile_checksum is not None and profile_checksum != self.profile_checksum:
            return False, "taste profile mismatch"
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
            )


def train_residual(
    pairs: list[ResidualPair],
    *,
    namespace: str,
    baseline: str,
    profile_checksum: str,
    evidence_revision: str,
    seed: int = 0,
    alpha: float = 0.5,
    delta_max: float = 0.75,
    min_subjects: int = 25,
    min_pairs: int = 200,
    min_improvement: float = 0.02,
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
    held_out = [pair for pair in pairs if split[pair.subject] in {"validation", "test"}]
    if not train or not held_out:
        return None, {"outcome": "no_change", "reason": "insufficient_held_out_subjects"}
    diffs = np.asarray(
        [[pair.winner[name] - pair.loser[name] for name in common] for pair in train],
        dtype=np.float64,
    )
    margins = np.asarray([pair.baseline_margin for pair in train], dtype=np.float64)
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
    held_diffs = np.asarray(
        [[pair.winner[name] - pair.loser[name] for name in common] for pair in held_out],
        dtype=np.float64,
    )
    held_margins = np.asarray([pair.baseline_margin for pair in held_out], dtype=np.float64)
    held_weights = np.asarray([pair.weight for pair in held_out], dtype=np.float64)
    corrections = alpha * np.clip(held_diffs @ coefficients, -delta_max, delta_max)
    denominator = float(held_weights.sum()) or 1.0
    baseline_accuracy = float(np.sum(held_weights * (held_margins > 0)) / denominator)
    candidate_accuracy = float(np.sum(held_weights * ((held_margins + corrections) > 0)) / denominator)
    evaluation = ResidualEvaluation(
        baseline_accuracy=baseline_accuracy,
        candidate_accuracy=candidate_accuracy,
        improvement=candidate_accuracy - baseline_accuracy,
        held_out_subjects=len({pair.subject for pair in held_out}),
        held_out_pairs=len(held_out),
        mean_abs_adjustment=float(np.mean(np.abs(corrections))),
        max_abs_adjustment=float(np.max(np.abs(corrections))),
    )
    report = {"outcome": "candidate", "evaluation": asdict(evaluation), "feature_names": common}
    if evaluation.improvement < min_improvement:
        return None, {**report, "outcome": "no_change", "reason": "no_held_out_improvement"}
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
        ),
        {**report, "outcome": "activate"},
    )
