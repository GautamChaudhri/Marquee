"""Train the Phase-1 learned head from the labels file.

Labels v2 (written by the feedback endpoint) embed the full normalized
feature vector per row, so training is a direct read — no dependency on a
run's working directory surviving a re-run. Legacy v1 rows
(``{title, orig_filename, label}``) are still supported via the original
join against ``data/runs/work/*/pipeline_run.json`` and the historical
``experiments/runs/*/pipeline_run.json`` trees.

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

from marquee.config import settings
from marquee.core.pipeline_config import pipeline_settings
from marquee.ml import feedback_store
from marquee.ml.learned_head import LogisticHead

_RUNS_DIR = settings.runs_work_path
_LEGACY_RUNS_DIRS = (
    Path(__file__).resolve().parents[2] / "experiments" / "runs",
    Path(__file__).resolve().parents[1] / "experiments" / "runs",
)


def collect_v1_samples(
    runs_dirs: tuple[Path, ...],
    labels: dict[tuple[str, str], int],
) -> list[tuple[dict[str, float], int]]:
    """Join v1 labels against the recorded normalized features of every run."""
    samples: list[tuple[dict[str, float], int]] = []
    if not labels:
        return samples
    for runs_dir in runs_dirs:
        if not runs_dir.exists():
            continue
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
    runs_dirs: tuple[Path, ...],
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

    samples.extend(collect_v1_samples(runs_dirs, v1_labels))

    if not samples:
        return np.empty((0, 0)), np.empty(0), [], len(movies)

    common = sorted(set.intersection(*(set(feat) for feat, _ in samples)))
    matrix = np.asarray(
        [[feat[name] for name in common] for feat, _ in samples], dtype=np.float64
    )
    targets = np.asarray([label for _, label in samples], dtype=np.float64)
    return matrix, targets, common, len(movies)


_BUCKET_FAV = "fav"
_BUCKET_INDIFF = "indiff"
_BUCKET_HATE = "hate"


def build_pairwise_training_data(
    rows: list[dict],
) -> tuple[np.ndarray, np.ndarray, list[str], int, int]:
    """Expand v3 ranking events into weighted within-movie preference pairs.

    Each ranking event encodes a partial order over a movie's candidates:
    ``Favorites`` (ordered tiers, ties within a tier) ≻ ``Indifferent`` ≻
    ``Hate``. We turn that into ``(x_winner - x_loser, weight)`` rows — one per
    cross-group/cross-tier pair, none within a tier (ties = no constraint).

    Weight = ``movie_norm × confidence`` where ``movie_norm = 1/pairs-in-movie``
    (so a 30-candidate movie can't drown a 6-candidate one) and ``confidence``
    is 1.0 for explicit pairs (between favorite tiers, favorite↔hate) and
    ``FEEDBACK_INDIFF_HATE_PAIR_WEIGHT`` for pairs touching the inferred
    indifferent set. Legacy v1/v2 rows are ignored here (decision: start fresh).

    Returns ``(diffs[N,F], weights[N], feature_names, n_movies, n_pairs)``.
    Feature names are the intersection present on every paired candidate.
    """
    implicit_weight = pipeline_settings.FEEDBACK_INDIFF_HATE_PAIR_WEIGHT
    all_pairs: list[tuple[dict, dict, float]] = []
    movies: set[object] = set()

    for row in rows:
        if row.get("v") != 3 or row.get("type") != "ranking":
            continue
        feats = {
            c["orig_filename"]: c["normalized_features"]
            for c in row.get("candidates", [])
            if c.get("orig_filename") and c.get("normalized_features")
        }

        # Ordered groups, best → worst, tagged explicit/implicit. Favorites
        # arrive as tiers (list of lists); indifferent + hate are single groups.
        groups: list[tuple[list[str], bool]] = []
        favorites_flat: set[str] = set()
        for tier in row.get("favorites") or []:
            members = [name for name in tier if name in feats]
            favorites_flat.update(tier)
            if members:
                groups.append((members, True))  # explicit
        hated = [name for name in (row.get("hated") or []) if name in feats]
        indifferent = [
            name
            for name in feats
            if name not in favorites_flat and name not in set(row.get("hated") or [])
        ]
        if indifferent:
            groups.append((indifferent, False))  # implicit (inferred)
        if hated:
            groups.append((hated, True))  # explicit

        movie_pairs: list[tuple[dict, dict, float]] = []
        for hi, (winners, w_explicit) in enumerate(groups):
            for losers, l_explicit in groups[hi + 1 :]:
                # Pair is explicit only when BOTH endpoints were stated by the
                # user (favorite or hate); anything touching indifferent is soft.
                confidence = 1.0 if (w_explicit and l_explicit) else implicit_weight
                for winner in winners:
                    for loser in losers:
                        movie_pairs.append((feats[winner], feats[loser], confidence))

        if not movie_pairs:
            continue
        movies.add(row.get("movie_id") if row.get("movie_id") is not None else row.get("title"))
        movie_norm = 1.0 / len(movie_pairs)
        all_pairs.extend(
            (winner, loser, confidence * movie_norm)
            for winner, loser, confidence in movie_pairs
        )

    if not all_pairs:
        return np.empty((0, 0)), np.empty(0), [], len(movies), 0

    common = sorted(
        set.intersection(
            *(set(winner) & set(loser) for winner, loser, _ in all_pairs)
        )
    )
    diffs = np.asarray(
        [
            [winner[name] - loser[name] for name in common]
            for winner, loser, _ in all_pairs
        ],
        dtype=np.float64,
    )
    weights = np.asarray([weight for _, _, weight in all_pairs], dtype=np.float64)
    return diffs, weights, common, len(movies), len(all_pairs)


def train_from_labels(
    *,
    runs_dir: Path | None = None,
    min_labels: int | None = None,
    min_movies: int | None = None,
    min_pairs: int | None = None,
    mode: str | None = None,
    l2: float = 1.0,
    save: bool = True,
) -> tuple[LogisticHead | None, dict]:
    """Train + (optionally) save the head. Returns (head|None, info).

    ``mode`` selects the trainer (defaults to ``HEAD_TRAIN_MODE``): "pairwise"
    learns a RankNet head from v3 ranking events; "pointwise" is the legacy
    logistic regression over v1/v2 approve/override labels.
    """
    mode = pipeline_settings.HEAD_TRAIN_MODE if mode is None else mode
    min_movies = pipeline_settings.HEAD_MIN_MOVIES if min_movies is None else min_movies
    rows = feedback_store.read_all()

    if mode == "pairwise":
        min_pairs = pipeline_settings.HEAD_MIN_PAIRS if min_pairs is None else min_pairs
        diffs, weights, names, n_movies, n_pairs = build_pairwise_training_data(rows)
        info = {
            "mode": "pairwise",
            "n_pairs": n_pairs,
            "n_movies": n_movies,
            "min_pairs": min_pairs,
            "min_movies": min_movies,
        }
        if n_pairs < min_pairs or n_movies < min_movies:
            info["activated"] = False
            info["reason"] = (
                f"need {min_movies} movies / {min_pairs} pairs; "
                f"have {n_movies} movies / {n_pairs} pairs"
            )
            return None, info
        head = LogisticHead.train_pairwise(diffs, weights, names, l2=l2)
        info["activated"] = True
        info["train_accuracy"] = head.train_accuracy
        info["features"] = names
        info["reason"] = "trained"
        if save:
            head.save()
        return head, info

    # Legacy pointwise path (v1/v2 approve/override labels).
    min_labels = pipeline_settings.HEAD_MIN_LABELS if min_labels is None else min_labels
    current_runs_dir = runs_dir or _RUNS_DIR
    runs_dirs = (current_runs_dir, *_LEGACY_RUNS_DIRS)

    features, targets, names, n_movies = build_training_data(rows, runs_dirs)
    n_samples = int(len(targets))
    info = {
        "mode": "pointwise",
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
    parser.add_argument("--runs-dir", type=Path, default=None)
    parser.add_argument(
        "--mode", choices=("pairwise", "pointwise"), default=pipeline_settings.HEAD_TRAIN_MODE
    )
    parser.add_argument("--min-labels", type=int, default=pipeline_settings.HEAD_MIN_LABELS)
    parser.add_argument("--min-pairs", type=int, default=pipeline_settings.HEAD_MIN_PAIRS)
    parser.add_argument("--min-movies", type=int, default=pipeline_settings.HEAD_MIN_MOVIES)
    parser.add_argument("--l2", type=float, default=1.0)
    args = parser.parse_args()

    head, info = train_from_labels(
        runs_dir=args.runs_dir or settings.runs_work_path,
        mode=args.mode,
        min_labels=args.min_labels,
        min_pairs=args.min_pairs,
        min_movies=args.min_movies,
        l2=args.l2,
    )
    if info.get("mode") == "pairwise":
        print(f"[INFO] {info['n_pairs']} pairs across {info['n_movies']} movies")
    else:
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
