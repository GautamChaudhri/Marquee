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
import threading
from math import log2
from pathlib import Path

import numpy as np

from marquee.config import settings
from marquee.core.cancellation import raise_if_cancelled
from marquee.core.pipeline_config import pipeline_settings
from marquee.ml import feedback_store
from marquee.ml.learned_head import LogisticHead, fit_scale_bias
from marquee.ml.namespaces import TasteNamespace, get_namespace

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
    matrix = np.asarray([[feat[name] for name in common] for feat, _ in samples], dtype=np.float64)
    targets = np.asarray([label for _, label in samples], dtype=np.float64)
    return matrix, targets, common, len(movies)


def _position_discount(rank: int) -> float:
    """DCG-style discount for a 1-based position in the user's FINAL order —
    top of the list matters more than the tail, regardless of whether `rank`
    came from a real model prediction (live runs) or an arbitrary baseline
    (onboarding's taste-test manifest order has no real prediction at all;
    see design/30) — the discount is defined purely on the final order, so
    the same formula applies either way with no special-casing."""
    return 1.0 / log2(rank + 1)


def _pair_magnitude(winner_final_rank: int, loser_final_rank: int) -> float:
    """How much one pair's correction matters, from where it landed in the
    final order. Average of both endpoints' discounts: symmetric, and a
    dramatic mover's pairs are dominated by its own high discount regardless
    of where the displaced posters end up, while a small adjacent swap's
    pairs stay low on both sides — verified against the design's worked
    examples (a rank-9->2 jump's pairs outweigh a rank-7<->8 swap's pair).
    Empirically unvalidated combination rule (vs. e.g. taking whichever
    endpoint is more extreme) — revisit once real ranking data exists.
    """
    return (_position_discount(winner_final_rank) + _position_discount(loser_final_rank)) / 2.0


def build_inversion_training_data(
    rows: list[dict],
) -> tuple[np.ndarray, np.ndarray, list[str], int, int]:
    """Expand v4 ranking events into weighted within-movie preference pairs.

    Each event stores a final ``order`` (best -> worst) plus a ``hated`` set;
    every candidate also carries a ``baseline_rank`` (the pipeline's own
    predicted rank for live runs, or an arbitrary manifest position for
    onboarding's taste test — see design/30). Pairs come from two sources:

    - **Inversions**: any pair of candidates still in ``order`` whose final
      relative order disagrees with their baseline relative order. Agreement
      produces nothing — the model already had that comparison right, so
      re-asserting it teaches nothing new. Confidence 1.0 (both endpoints are
      in the list the user actively arranged).
    - **Hate-pile pairs**: a hated candidate vs. every surviving candidate the
      baseline had ranked *worse* than it (the hate action contradicts that
      prediction). Confidence ``FEEDBACK_INDIFF_HATE_PAIR_WEIGHT`` (one click,
      not an explicit per-item comparison). No pair where the baseline
      already agreed the hated poster was worse — no contradiction there.
      The hated poster's own position, for discount purposes only, is
      ``len(order) + 1`` (one past the end — hate places you at the bottom).

    Weight = ``movie_norm * confidence * magnitude`` (see ``_pair_magnitude``
    for the position-discount). ``movie_norm`` normalizes by the *sum of a
    movie's raw magnitudes*, not its raw pair count — dividing by pair count
    alone would make a 1-swap and a 7-jump *from the same movie* land on the
    same per-pair weight (both share one denominator), which defeats the
    point of the magnitude term. Normalizing by the magnitude-weighted total
    still caps each movie's total contribution to a constant (so a
    30-candidate movie can't drown a 6-candidate one — the original purpose),
    while preserving the relative weight of a big jump vs a small swap within
    that movie's own budget.

    Returns ``(diffs[N,F], weights[N], feature_names, n_movies, n_pairs)``.
    Feature names are the intersection present on every paired candidate.
    """
    implicit_weight = pipeline_settings.FEEDBACK_INDIFF_HATE_PAIR_WEIGHT
    all_pairs: list[tuple[dict, dict, float]] = []
    movies: set[object] = set()

    for row in rows:
        if row.get("v") != 4 or row.get("type") != "ranking":
            continue

        order = [c for c in (row.get("order") or []) if c.get("normalized_features")]
        hated = [c for c in (row.get("hated") or []) if c.get("normalized_features")]
        final_rank = {c["orig_filename"]: i + 1 for i, c in enumerate(order)}
        feats = {c["orig_filename"]: c["normalized_features"] for c in order}

        movie_pairs: list[tuple[dict, dict, float]] = []

        # Orderable-list inversions: i < j in final order, so order[i] is the
        # final winner over order[j]. An inversion exists only when the
        # baseline preferred the loser (lower baseline_rank = better).
        for i, winner_c in enumerate(order):
            w_baseline = winner_c.get("baseline_rank")
            if w_baseline is None:
                continue
            for loser_c in order[i + 1 :]:
                l_baseline = loser_c.get("baseline_rank")
                if l_baseline is None or l_baseline >= w_baseline:
                    continue  # baseline agreed (or ties) — no signal
                w_name, l_name = winner_c["orig_filename"], loser_c["orig_filename"]
                magnitude = _pair_magnitude(final_rank[w_name], final_rank[l_name])
                movie_pairs.append((feats[w_name], feats[l_name], magnitude))

        # Hate-pile pairs: hated vs. every survivor the baseline ranked worse
        # than the hated poster (contradicting that prediction).
        virtual_rank = len(order) + 1
        for h in hated:
            h_baseline = h.get("baseline_rank")
            if h_baseline is None:
                continue
            for s in order:
                s_baseline = s.get("baseline_rank")
                if s_baseline is None or h_baseline >= s_baseline:
                    continue  # baseline already agreed hated < s — no contradiction
                magnitude = _pair_magnitude(final_rank[s["orig_filename"]], virtual_rank)
                movie_pairs.append(
                    (
                        feats[s["orig_filename"]],
                        h["normalized_features"],
                        implicit_weight * magnitude,
                    )
                )

        if not movie_pairs:
            continue
        movies.add(row.get("movie_id") if row.get("movie_id") is not None else row.get("title"))
        total_raw = sum(weight for _, _, weight in movie_pairs)
        movie_norm = 1.0 / total_raw if total_raw > 0 else 0.0
        all_pairs.extend(
            (winner, loser, weight * movie_norm) for winner, loser, weight in movie_pairs
        )

    if not all_pairs:
        return np.empty((0, 0)), np.empty(0), [], len(movies), 0

    common = sorted(set.intersection(*(set(winner) & set(loser) for winner, loser, _ in all_pairs)))
    diffs = np.asarray(
        [[winner[name] - loser[name] for name in common] for winner, loser, _ in all_pairs],
        dtype=np.float64,
    )
    weights = np.asarray([weight for _, _, weight in all_pairs], dtype=np.float64)
    return diffs, weights, common, len(movies), len(all_pairs)


def build_pointwise_pseudo_labels(
    rows: list[dict],
    feature_names: list[str],
) -> tuple[np.ndarray, np.ndarray, int]:
    """Derive 0/1 pseudo-labels from the same v4 ranking events, for Platt
    calibration of an inversion-trained head (see ``LogisticHead.calibrated``).

    The top slice of each event's final order (the same cutoff used for
    positive-exemplar staging — see ``feedback_store.positive_exemplar_count``)
    -> 1; hated candidates -> 0. Everything else in the order is an untouched
    relative position, not a stated preference, and is skipped. Only
    candidates carrying every name in ``feature_names`` are included.

    Returns ``(X[N, len(feature_names)], y[N], n_movies)``.
    """
    samples: list[tuple[dict, int]] = []
    movies: set[object] = set()

    for row in rows:
        if row.get("v") != 4 or row.get("type") != "ranking":
            continue
        order = row.get("order") or []
        hated = row.get("hated") or []
        n_pos = feedback_store.positive_exemplar_count(len(order))

        row_samples = [
            (c["normalized_features"], 1)
            for c in order[:n_pos]
            if c.get("normalized_features")
            and all(f in c["normalized_features"] for f in feature_names)
        ] + [
            (c["normalized_features"], 0)
            for c in hated
            if c.get("normalized_features")
            and all(f in c["normalized_features"] for f in feature_names)
        ]
        if not row_samples:
            continue
        movies.add(row.get("movie_id") if row.get("movie_id") is not None else row.get("title"))
        samples.extend(row_samples)

    if not samples:
        return np.empty((0, len(feature_names))), np.empty(0), 0

    x = np.asarray(
        [[feat[name] for name in feature_names] for feat, _ in samples], dtype=np.float64
    )
    y = np.asarray([label for _, label in samples], dtype=np.float64)
    return x, y, len(movies)


def train_from_labels(
    *,
    runs_dir: Path | None = None,
    min_labels: int | None = None,
    min_movies: int | None = None,
    min_pairs: int | None = None,
    mode: str | None = None,
    l2: float = 1.0,
    save: bool = True,
    cancel_event: threading.Event | None = None,
    namespace: TasteNamespace | None = None,
) -> tuple[LogisticHead | None, dict]:
    """Train + (optionally) save the head. Returns (head|None, info).

    ``mode`` selects the trainer (defaults to ``HEAD_TRAIN_MODE``): "pairwise"
    learns a RankNet head from v4 ranking events (rank-inversion pairs —
    design/30); "pointwise" is the legacy logistic regression over v1/v2
    approve/override labels.
    """
    ns = namespace or get_namespace("movies")
    mode = pipeline_settings.HEAD_TRAIN_MODE if mode is None else mode
    min_movies = pipeline_settings.HEAD_MIN_MOVIES if min_movies is None else min_movies
    raise_if_cancelled(cancel_event, "learned head training cancelled")
    rows = feedback_store.read_all(ns)
    raise_if_cancelled(cancel_event, "learned head training cancelled")

    if mode == "pairwise":
        min_pairs = pipeline_settings.HEAD_MIN_PAIRS if min_pairs is None else min_pairs
        diffs, weights, names, n_movies, n_pairs = build_inversion_training_data(rows)
        raise_if_cancelled(cancel_event, "learned head training cancelled")
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
        head = LogisticHead.train_pairwise(diffs, weights, names, l2=l2, cancel_event=cancel_event)

        # The RankNet objective above only learns ordering (bias is fixed at
        # 0.0 — see train_pairwise's docstring), which lets raw scores
        # saturate sigmoid() for every candidate. Calibrate scale+bias against
        # the same events' favorite/hated candidates as pointwise 0/1 labels
        # so the absolute score is meaningful too, without touching the order.
        calib_x, calib_y, _ = build_pointwise_pseudo_labels(rows, head.feature_names)
        info["calibrated"] = False
        if len(calib_y) >= 10 and len(np.unique(calib_y)) == 2:
            raw_scores = calib_x @ head.weights
            scale, bias = fit_scale_bias(raw_scores, calib_y, cancel_event=cancel_event)
            if scale > 0:
                head = head.calibrated(scale, bias)
                info["calibrated"] = True
                info["n_calibration_samples"] = int(len(calib_y))

        info["activated"] = True
        info["train_accuracy"] = head.train_accuracy
        info["features"] = names
        info["reason"] = "trained"
        if save:
            head.save(ns.head_path)
        return head, info

    # Legacy pointwise path (v1/v2 approve/override labels).
    min_labels = pipeline_settings.HEAD_MIN_LABELS if min_labels is None else min_labels
    current_runs_dir = runs_dir or _RUNS_DIR
    runs_dirs = (current_runs_dir, *_LEGACY_RUNS_DIRS)

    features, targets, names, n_movies = build_training_data(rows, runs_dirs)
    raise_if_cancelled(cancel_event, "learned head training cancelled")
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

    head = LogisticHead.train(features, targets, names, l2=l2, cancel_event=cancel_event)
    info["activated"] = True
    info["train_accuracy"] = head.train_accuracy
    info["features"] = names
    info["reason"] = "trained"
    if save:
        head.save(ns.head_path)
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
    print(
        f"[INFO] SCORER=auto will now use the learned head ({pipeline_settings.LEARNED_HEAD_PATH.name})"
    )


if __name__ == "__main__":
    main()
