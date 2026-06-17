"""Phase-1 learned ranking head: logistic regression over normalized features.

The Phase-0 weighted scorer encodes hand-tuned intuition; this head learns
the weights from the user's actual decisions. Labels follow the design
(04 §5): an approved/selected poster -> 1, the auto-pick the user overrode
-> 0, everything else unlabeled. Until the feedback UI exists, labels are
collected in a JSONL file (see ``head_trainer.py``).

Implementation notes:
  - Pure numpy L2-regularized logistic regression (full-batch gradient
    descent with early stopping) — no sklearn dependency; deterministic and
    tiny (the feature space is ~12 dims).
  - The artifact records the exact feature names it was trained on, the
    embedding model name, and sample counts. ``LearnedScorer`` validates all
    of that on load and falls back loudly rather than scoring garbage.
  - Inputs are the *normalized* features (already 0..1, comparable scales),
    the same values the weighted scorer consumes — so both heads are
    interchangeable behind the PosterScorer interface.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from marquee.core.pipeline_config import pipeline_settings
from marquee.ml.artifact_codec import (
    decode_unicode_list,
    decode_unicode_scalar,
    ensure_safe_artifact,
    load_npz_safe,
    save_npz_atomic,
    unicode_array,
    unicode_scalar,
)

logger = logging.getLogger(__name__)


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


@dataclass
class LogisticHead:
    """A trained logistic ranking head."""

    feature_names: list[str]
    weights: np.ndarray  # (F,)
    bias: float
    model_name: str
    n_samples: int
    train_accuracy: float
    trained_at: str

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    @classmethod
    def train(
        cls,
        features: np.ndarray,
        labels: np.ndarray,
        feature_names: list[str],
        *,
        l2: float = 1.0,
        learning_rate: float = 0.5,
        max_iterations: int = 5000,
        tolerance: float = 1e-7,
        model_name: str | None = None,
    ) -> LogisticHead:
        """Full-batch gradient descent with early stopping on loss delta."""
        x = np.asarray(features, dtype=np.float64)
        y = np.asarray(labels, dtype=np.float64)
        if x.ndim != 2 or x.shape[0] != y.shape[0]:
            raise ValueError(f"Bad training shapes: X={x.shape}, y={y.shape}")
        if x.shape[1] != len(feature_names):
            raise ValueError("feature_names length does not match X columns")
        if len(np.unique(y)) < 2:
            raise ValueError("Training labels must contain both classes (0 and 1)")

        n, f = x.shape
        weights = np.zeros(f)
        bias = 0.0
        previous_loss = np.inf
        for _ in range(max_iterations):
            probabilities = _sigmoid(x @ weights + bias)
            error = probabilities - y
            grad_w = (x.T @ error) / n + l2 * weights / n
            grad_b = float(error.mean())
            weights -= learning_rate * grad_w
            bias -= learning_rate * grad_b

            loss = float(
                -np.mean(
                    y * np.log(probabilities + 1e-12)
                    + (1 - y) * np.log(1 - probabilities + 1e-12)
                )
                + l2 * float(weights @ weights) / (2 * n)
            )
            if abs(previous_loss - loss) < tolerance:
                break
            previous_loss = loss

        predictions = (_sigmoid(x @ weights + bias) >= 0.5).astype(np.float64)
        accuracy = float((predictions == y).mean())
        return cls(
            feature_names=list(feature_names),
            weights=weights.astype(np.float64),
            bias=float(bias),
            model_name=model_name or pipeline_settings.AI_MODEL,
            n_samples=n,
            train_accuracy=accuracy,
            trained_at=datetime.now(UTC).isoformat(),
        )

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def score(self, normalized: dict[str, float]) -> tuple[float, dict[str, float]]:
        """P(user picks this) plus per-feature w*x products for the logs.

        Missing features raise: a head trained on dino_knn cannot silently
        score a run where dino is disabled — that is a config mismatch the
        user must see (retrain, or force SCORER=weighted).
        """
        missing = [name for name in self.feature_names if name not in normalized]
        if missing:
            raise RuntimeError(
                f"Learned head expects features {missing} that this run did not "
                "compute. Retrain the head or set SCORER=weighted."
            )
        x = np.asarray(
            [normalized[name] for name in self.feature_names], dtype=np.float64
        )
        probability = float(_sigmoid(np.asarray([x @ self.weights + self.bias]))[0])
        contributions = {
            name: float(weight * value)
            for name, weight, value in zip(self.feature_names, self.weights, x, strict=True)
        }
        return probability, contributions

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str | Path | None = None) -> Path:
        artifact = Path(path or pipeline_settings.LEARNED_HEAD_PATH)
        save_npz_atomic(
            artifact,
            {
                "feature_names": unicode_array(self.feature_names),
                "weights": self.weights,
                "bias": np.float64(self.bias),
                "model_name": unicode_scalar(self.model_name),
                "n_samples": np.int64(self.n_samples),
                "train_accuracy": np.float64(self.train_accuracy),
                "trained_at": unicode_scalar(self.trained_at),
            },
        )
        logger.info("Saved learned head (%d samples) to %s", self.n_samples, artifact)
        return artifact

    @classmethod
    def load(
        cls,
        path: str | Path | None = None,
        *,
        expected_model_name: str | None = None,
    ) -> LogisticHead:
        artifact = Path(path or pipeline_settings.LEARNED_HEAD_PATH)
        if not artifact.exists():
            raise FileNotFoundError(f"Learned head not found: {artifact}")
        ensure_safe_artifact(artifact, "learned_head")
        with load_npz_safe(artifact) as data:
            stored_model = decode_unicode_scalar(data["model_name"])
            expected = expected_model_name or pipeline_settings.AI_MODEL
            if stored_model != expected:
                raise RuntimeError(
                    f"Learned head model mismatch: artifact={stored_model!r}, "
                    f"configured={expected!r}. Retrain the head."
                )
            return cls(
                feature_names=decode_unicode_list(data["feature_names"]),
                weights=np.asarray(data["weights"], dtype=np.float64),
                bias=float(data["bias"]),
                model_name=stored_model,
                n_samples=int(data["n_samples"]),
                train_accuracy=float(data["train_accuracy"]),
                trained_at=decode_unicode_scalar(data["trained_at"]),
            )
