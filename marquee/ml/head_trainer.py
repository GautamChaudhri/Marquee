"""Train the Phase-1 learned head from the labels file.

Labels v2 (written by the feedback endpoint) embed the full normalized
feature vector per row, so training is a direct read — no dependency on a
run's working directory surviving a re-run. Legacy v1 rows
(``{title, orig_filename, label}``) are still supported via the original
join against ``experiments/runs/*/pipeline_run.json``.

Semantics (design 04 §5): 1 = approved/selected; 0 = overrode/rejected.

Usage:

    python -m marquee.ml.head_trainer            # train + save if enough labels
    python -m marquee.ml.head_trainer --min-labels 10

Once the artifact exists, SCORER=auto switches the pipeline to the learned
head automatically. The feedback endpoint calls ``train_from_labels()``
directly after each event when HEAD_AUTO_RETRAIN is on.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from marquee.core.pipeline_config import pipeline_settings
from marquee.ml import feedback_store
from marquee.ml.learned_head import LogisticHead

_RUNS_DIR = Path(__file__).resolve().parents[1] / "experiments" / "runs"


def collect_v1_samples(
    runs_dir: Path,
    labels: dict[tuple[str, str], int],
) -> list[tuple[dict[str, float], int]]:
    """Join v1 labels against the recorded normalized features of every run."""
    samples: list[tuple[dict[str, float], int]] = []
    if not labels:
        return samples
    for run_json in sorted(runs_dir.glob("*/pipeline_run.json")):
        try:
            payload = json.loads(run_json.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[WARN] Skipping unreadable {run_json}: {exc}")
            continue
        title = payload.get("title", "")
        if payload.get("model_name") != pipeline_settings.AI_MODEL:
            continue
        for candidate in payload.get("candidates", []):
            key = (title, candidate.get("orig_filename", ""))
            if key not in labels:
                continue
            normalized = candidate.get("normalized_features")
            if normalized:
                samples.append((normalized, labels[key]))
    return samples


def build_training_data(
    rows: list[dict],
    runs_dir: Path,
) -> tuple[np.ndarray, np.ndarray, list[str], int]:
    """Combine v2 (embedded features) + v1 (run-join) rows into a matrix.

    Returns (X, y, feature_names, n_distinct_movies). Uses the features
    present in *every* sample so the head never trains on a feature that
    exists for some runs but not others (e.g. dino on/off).
    """
    samples: list[tuple[dict[str, float], int]] = []
    movies: set[object] = set()
    v1_labels: dict[tuple[str, str], int] = {}

    for row in rows:
        label = row.get("label")
        if label is None:
            continue
        movies.add(row.get("movie_id") if row.get("movie_id") is not None else row.get("title"))
        if row.get("v") == 2:
            normalized = row.get("normalized_features")
            if normalized:
                samples.append((normalized, int(label)))
        else:
            try:
                v1_labels[(row["title"], row["orig_filename"])] = int(label)
            except (KeyError, ValueError, TypeError):
                continue

    samples.extend(collect_v1_samples(runs_dir, v1_labels))

    if not samples:
        return np.empty((0, 0)), np.empty(0), [], len(movies)

    common = sorted(set.intersection(*(set(feat) for feat, _ in samples)))
    matrix = np.asarray(
        [[feat[name] for name in common] for feat, _ in samples], dtype=np.float64
    )
    targets = np.asarray([label for _, label in samples], dtype=np.float64)
    return matrix, targets, common, len(movies)


def train_from_labels(
    *,
    runs_dir: Path = _RUNS_DIR,
    min_labels: int | None = None,
    min_movies: int | None = None,
    l2: float = 1.0,
    save: bool = True,
) -> tuple[LogisticHead | None, dict]:
    """Train + (optionally) save the head. Returns (head|None, info)."""
    min_labels = pipeline_settings.HEAD_MIN_LABELS if min_labels is None else min_labels
    min_movies = pipeline_settings.HEAD_MIN_MOVIES if min_movies is None else min_movies

    rows = feedback_store.read_all()
    features, targets, names, n_movies = build_training_data(rows, runs_dir)
    n_samples = int(len(targets))
    info = {
        "n_samples": n_samples,
        "n_movies": n_movies,
        "min_labels": min_labels,
        "min_movies": min_movies,
    }

    if n_samples < min_labels or n_movies < min_movies:
        info["activated"] = False
        info["reason"] = (
            f"need {min_movies} movies / {min_labels} labels; "
            f"have {n_movies} movies / {n_samples} labels"
        )
        return None, info

    if len(np.unique(targets)) < 2:
        info["activated"] = False
        info["reason"] = "labels must contain both classes (0 and 1)"
        return None, info

    head = LogisticHead.train(features, targets, names, l2=l2)
    info["activated"] = True
    info["train_accuracy"] = head.train_accuracy
    info["features"] = names
    info["reason"] = "trained"
    if save:
        head.save()
    return head, info


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, default=_RUNS_DIR)
    parser.add_argument("--min-labels", type=int, default=pipeline_settings.HEAD_MIN_LABELS)
    parser.add_argument("--min-movies", type=int, default=pipeline_settings.HEAD_MIN_MOVIES)
    parser.add_argument("--l2", type=float, default=1.0)
    args = parser.parse_args()

    head, info = train_from_labels(
        runs_dir=args.runs_dir,
        min_labels=args.min_labels,
        min_movies=args.min_movies,
        l2=args.l2,
    )
    print(f"[INFO] {info['n_samples']} labels across {info['n_movies']} movies")
    if head is None:
        raise SystemExit(f"[ABORT] {info['reason']}")

    print(f"[INFO] Train accuracy: {head.train_accuracy:.3f}")
    print("[INFO] Learned weights (your taste, read out):")
    for name, weight in sorted(
        zip(head.feature_names, head.weights, strict=True),
        key=lambda item: -abs(item[1]),
    ):
        print(f"  {name:>22s}: {weight:+.4f}")
    print(f"[INFO] SCORER=auto will now use the learned head ({pipeline_settings.LEARNED_HEAD_PATH.name})")


if __name__ == "__main__":
    main()
