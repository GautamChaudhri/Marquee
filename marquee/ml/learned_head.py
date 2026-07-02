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

import dataclasses
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from marquee.core.jobs.cancel_registry import raise_if_cancelled
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


def fit_scale_bias(
    raw_scores: np.ndarray,
    labels: np.ndarray,
    *,
    learning_rate: float = 0.5,
    max_iterations: int = 5000,
    tolerance: float = 1e-9,
    cancel_event: threading.Event | None = None,
) -> tuple[float, float]:
    """Platt-scale raw RankNet scores (``x·w``) against true 0/1 labels.

    Fits ``a, b`` so ``sigmoid(a*s + b)`` matches the labels' actual
    distribution, instead of letting an uncalibrated ``sigmoid(s)`` saturate
    when ``s`` runs large (which is exactly what a pairwise-trained head with
    no bias term produces — see ``LogisticHead.train_pairwise``). ``a`` is
    initialized scaled to the data so convergence doesn't depend on how large
    the raw scores happen to be.
    """
    s = np.asarray(raw_scores, dtype=np.float64)
    y = np.asarray(labels, dtype=np.float64)
    spread = float(s.std())
    a = 1.0 / spread if spread > 1e-9 else 1.0
    b = 0.0
    previous_loss = np.inf
    for iteration in range(max_iterations):
        if iteration % 100 == 0:
            raise_if_cancelled(cancel_event, "learned head training cancelled")
        probabilities = _sigmoid(a * s + b)
        error = probabilities - y
        grad_a = float((error * s).mean())
        grad_b = float(error.mean())
        a -= learning_rate * grad_a
        b -= learning_rate * grad_b

        loss = float(
            -np.mean(
                y * np.log(probabilities + 1e-12) + (1 - y) * np.log(1 - probabilities + 1e-12)
            )
        )
        if abs(previous_loss - loss) < tolerance:
            break
        previous_loss = loss
    return a, b


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
        cancel_event: threading.Event | None = None,
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
        for iteration in range(max_iterations):
            if iteration % 100 == 0:
                raise_if_cancelled(cancel_event, "learned head training cancelled")
            probabilities = _sigmoid(x @ weights + bias)
            error = probabilities - y
            grad_w = (x.T @ error) / n + l2 * weights / n
            grad_b = float(error.mean())
            weights -= learning_rate * grad_w
            bias -= learning_rate * grad_b

            loss = float(
                -np.mean(
                    y * np.log(probabilities + 1e-12) + (1 - y) * np.log(1 - probabilities + 1e-12)
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

    @classmethod
    def train_pairwise(
        cls,
        diffs: np.ndarray,
        weights: np.ndarray,
        feature_names: list[str],
        *,
        l2: float = 1.0,
        learning_rate: float = 0.5,
        max_iterations: int = 5000,
        tolerance: float = 1e-7,
        model_name: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> LogisticHead:
        """RankNet head: learn ``w`` so ``w·x`` orders posters as the user ranked.

        Each row of ``diffs`` is ``x_winner - x_loser`` for one within-movie
        preference pair; ``weights`` carries the per-pair importance (movie
        normalization × explicit/implicit confidence). The target is always 1
        (the winner should outscore the loser), so the loss is the weighted
        binary cross-entropy of ``sigmoid(w·d)`` against 1.

        No bias is learned: a bias shifts every poster's score equally and so
        cannot change a within-movie ranking (it cancels in ``x⁺ - x⁻``). The
        artifact therefore stores ``bias=0.0`` and the inference scorer
        (``score``) is unchanged.
        """
        d = np.asarray(diffs, dtype=np.float64)
        w_sample = np.asarray(weights, dtype=np.float64)
        if d.ndim != 2 or d.shape[0] != w_sample.shape[0]:
            raise ValueError(f"Bad pairwise shapes: diffs={d.shape}, weights={w_sample.shape}")
        if d.shape[1] != len(feature_names):
            raise ValueError("feature_names length does not match diffs columns")
        if d.shape[0] == 0:
            raise ValueError("No preference pairs to train on")

        n, f = d.shape
        weight_total = float(w_sample.sum()) or float(n)
        coef = np.zeros(f)
        previous_loss = np.inf
        for iteration in range(max_iterations):
            if iteration % 100 == 0:
                raise_if_cancelled(cancel_event, "learned head training cancelled")
            probabilities = _sigmoid(d @ coef)
            # ∂/∂w of weighted BCE(target=1): -weight·(1-p)·d, plus L2.
            grad = -(((1.0 - probabilities) * w_sample) @ d) / weight_total
            grad += l2 * coef / n
            coef -= learning_rate * grad

            loss = float(
                -np.sum(w_sample * np.log(probabilities + 1e-12)) / weight_total
                + l2 * float(coef @ coef) / (2 * n)
            )
            if abs(previous_loss - loss) < tolerance:
                break
            previous_loss = loss

        # Pairwise agreement: fraction of pairs the learned scorer orders right.
        agreement = float((_sigmoid(d @ coef) >= 0.5).mean())
        return cls(
            feature_names=list(feature_names),
            weights=coef.astype(np.float64),
            bias=0.0,
            model_name=model_name or pipeline_settings.AI_MODEL,
            n_samples=n,
            train_accuracy=agreement,
            trained_at=datetime.now(UTC).isoformat(),
        )

    # ------------------------------------------------------------------
    # Calibration
    # ------------------------------------------------------------------

    def calibrated(self, scale: float, bias: float) -> LogisticHead:
        """Return a copy with ``weights`` rescaled and ``bias`` replaced.

        ``scale`` must be positive: a positive rescale of ``x·w`` cannot
        change which of two candidates scores higher, so this can only
        recalibrate the absolute probability — never the ranking that
        ``train_pairwise()`` already learned.
        """
        if scale <= 0:
            raise ValueError("calibration scale must be positive")
        return dataclasses.replace(self, weights=self.weights * scale, bias=float(bias))

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
        x = np.asarray([normalized[name] for name in self.feature_names], dtype=np.float64)
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
