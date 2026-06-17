"""Hold-out evaluation of the taste profile's k-NN knobs.

Implements the classic cross-validation recipe for picking k: split the
hand-picked exemplars into folds, score each held-out exemplar against ONLY
the remaining exemplars (it can never match itself), and sweep
k / weighting / temperature to see which combination scores the held-out
posters the way we know they should be scored — high, and above the style
gate.

Because knn_sim is a score (not a class label), "classified correctly" has
two measurable meanings here:

  1. Held-out exemplars are known-liked posters the profile has never seen.
     They should score HIGH — and none should fall below GATE_MIN_KNN_SIM,
     or the style gate would reject a poster we know we like.
  2. The labeled feedback set (`data/feedback/labels.jsonl` by default) holds real
     pipeline candidates the user kept (label=1) or flagged (label=0). A good
     combo separates them: AUC = probability a random kept poster outscores a
     random flagged one (1.0 = perfect, 0.5 = coin flip).

Everything runs from the profile artifact and the embedding cache — no ONNX
inference, so a full sweep takes under a second.

Run:  python -m marquee.ml.knn_eval [--folds 5] [--seed 42]
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from marquee.core.pipeline_config import pipeline_settings
from marquee.ml.artifact_codec import (
    decode_unicode_list,
    ensure_safe_artifact,
    load_npz_safe,
)
from marquee.ml.taste_store import weighted_topk_mean

DEFAULT_KS = [1, 2, 3, 4, 5, 7, 10, 15, 20, 30, 50]
DEFAULT_TEMPS = [0.03, 0.05, 0.1, 0.2, 0.5]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_profile(path: Path) -> dict[str, np.ndarray]:
    if not path.exists():
        raise FileNotFoundError(
            f"Taste profile not found: {path}. "
            "Rebuild it with `python -m marquee.ml.taste_trainer`."
        )
    out: dict[str, np.ndarray] = {}
    ensure_safe_artifact(path, "taste_profile")
    with load_npz_safe(path) as data:
        out["embeddings"] = np.asarray(data["embeddings"], dtype=np.float32)
        out["poster_names"] = np.asarray(
            decode_unicode_list(data["poster_names"]), dtype=np.str_
        )
        if "dino_embeddings" in data:
            out["dino_embeddings"] = np.asarray(data["dino_embeddings"], dtype=np.float32)
    return out


def load_labeled_embeddings(
    labels_path: Path,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Embeddings of label=1 (kept) and label=0 (flagged) posters from the
    pipeline's embedding cache. Returns (kept, flagged, missing_count)."""
    if not labels_path.exists():
        return np.empty((0, 512)), np.empty((0, 512)), 0

    labels: dict[str, int] = {}
    for line in labels_path.read_text().splitlines():
        if line.strip():
            record = json.loads(line)
            labels[record["orig_filename"]] = int(record["label"])

    cache_dir = pipeline_settings.EMBEDDING_CACHE_DIR / pipeline_settings.AI_MODEL
    kept, flagged, missing = [], [], 0
    for orig_filename, label in labels.items():
        key = hashlib.sha256(f"{pipeline_settings.AI_MODEL}:{orig_filename}".encode()).hexdigest()
        cache_path = cache_dir / f"{key}.npz"
        if not cache_path.exists():
            missing += 1
            continue
        with np.load(cache_path, allow_pickle=False) as cached:
            vector = np.asarray(cached["embedding"], dtype=np.float32).reshape(-1)
        vector /= max(float(np.linalg.norm(vector)), 1e-10)
        (kept if label == 1 else flagged).append(vector)

    return (
        np.asarray(kept, dtype=np.float32).reshape(len(kept), -1),
        np.asarray(flagged, dtype=np.float32).reshape(len(flagged), -1),
        missing,
    )


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def holdout_scores(
    embeddings: np.ndarray,
    folds: int,
    seed: int,
    k: int,
    weighting: str,
    temperature: float,
) -> np.ndarray:
    """Score every exemplar against the profile MINUS its own fold."""
    count = embeddings.shape[0]
    rng = np.random.default_rng(seed)
    fold_of = rng.permutation(count) % folds
    sims = embeddings @ embeddings.T
    scores = np.empty(count, dtype=np.float64)
    for fold in range(folds):
        held = np.flatnonzero(fold_of == fold)
        train = np.flatnonzero(fold_of != fold)
        for index in held:
            scores[index] = weighted_topk_mean(
                sims[index, train],
                k,
                weighting=weighting,
                temperature=temperature,
            )
    return scores


def profile_scores(
    candidates: np.ndarray,
    exemplars: np.ndarray,
    k: int,
    weighting: str,
    temperature: float,
) -> np.ndarray:
    """Score external candidates against the FULL profile (runtime behavior)."""
    if candidates.size == 0:
        return np.empty(0)
    sims = candidates @ exemplars.T
    return np.asarray(
        [weighted_topk_mean(row, k, weighting=weighting, temperature=temperature) for row in sims]
    )


def auc(positives: np.ndarray, negatives: np.ndarray) -> float:
    """Tie-aware Mann-Whitney AUC: P(random positive > random negative)."""
    if positives.size == 0 or negatives.size == 0:
        return float("nan")
    scores = np.concatenate([positives, negatives])
    _, inverse, counts = np.unique(scores, return_inverse=True, return_counts=True)
    average_ranks = (np.cumsum(counts) - (counts - 1) / 2.0)[inverse]
    rank_sum = average_ranks[: positives.size].sum()
    expected_min = positives.size * (positives.size + 1) / 2.0
    return float((rank_sum - expected_min) / (positives.size * negatives.size))


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------


def run_sweep(args: argparse.Namespace) -> None:
    profile = load_profile(pipeline_settings.TASTE_PROFILE_PATH)
    embeddings = profile["embeddings"]
    kept, flagged, missing = load_labeled_embeddings(
        Path(pipeline_settings.FEEDBACK_LABELS_PATH)
    )
    gate = pipeline_settings.GATE_MIN_KNN_SIM

    print(f"Exemplars: {embeddings.shape[0]}  |  folds: {args.folds}  |  gate: knn_sim >= {gate}")
    print(
        f"Labeled feedback: {kept.shape[0]} kept, {flagged.shape[0]} flagged"
        + (f" ({missing} not in embedding cache)" if missing else "")
    )
    print(
        f"Active config: k={pipeline_settings.K_NEIGHBORS} "
        f"weighting={pipeline_settings.KNN_WEIGHTING} "
        f"temp={pipeline_settings.KNN_SOFTMAX_TEMP}\n"
    )

    combos: list[tuple[str, float]] = [("mean", 0.0)]
    combos += [("softmax", temp) for temp in args.temps]

    header = (
        f"{'weighting':<9} {'temp':>5} {'k':>3} | "
        f"{'ho_mean':>7} {'ho_p5':>7} {'ho_min':>7} {'gate_miss':>9} | "
        f"{'keep':>6} {'flag':>6} {'AUC':>6}"
    )
    best: tuple[float, float, str] | None = None
    for weighting, temperature in combos:
        print(header)
        for k in args.ks:
            held = holdout_scores(embeddings, args.folds, args.seed, k, weighting, temperature)
            kept_scores = profile_scores(kept, embeddings, k, weighting, temperature)
            flag_scores = profile_scores(flagged, embeddings, k, weighting, temperature)
            gate_miss = float((held < gate).mean())
            separation = auc(kept_scores, flag_scores)
            row = (
                f"{weighting:<9} {temperature:>5.2f} {k:>3} | "
                f"{held.mean():>7.4f} {np.percentile(held, 5):>7.4f} "
                f"{held.min():>7.4f} {gate_miss:>8.1%} | "
                f"{np.nanmean(kept_scores) if kept_scores.size else float('nan'):>6.3f} "
                f"{np.nanmean(flag_scores) if flag_scores.size else float('nan'):>6.3f} "
                f"{separation:>6.3f}"
            )
            print(row)
            if not np.isnan(separation):
                candidate = (separation, -gate_miss, f"{weighting} temp={temperature} k={k}")
                if best is None or candidate > best:
                    best = candidate
        print()

    if best is not None:
        print(f"Best keep/flag separation: {best[2]} (AUC={best[0]:.3f}, gate_miss={-best[1]:.1%})")

    if "dino_embeddings" in profile and args.dino:
        print("\nDINOv2 space (hold-out only — no cached candidate embeddings):")
        dino = profile["dino_embeddings"]
        print(f"{'k':>3} | {'ho_mean':>7} {'ho_p5':>7} {'ho_min':>7}")
        for k in args.ks:
            held = holdout_scores(
                dino,
                args.folds,
                args.seed,
                k,
                pipeline_settings.KNN_WEIGHTING,
                pipeline_settings.KNN_SOFTMAX_TEMP,
            )
            print(f"{k:>3} | {held.mean():>7.4f} {np.percentile(held, 5):>7.4f} {held.min():>7.4f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--folds", type=int, default=5, help="cross-validation folds (default 5)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--ks",
        type=lambda s: [int(v) for v in s.split(",")],
        default=DEFAULT_KS,
        help="comma-separated k values",
    )
    parser.add_argument(
        "--temps",
        type=lambda s: [float(v) for v in s.split(",")],
        default=DEFAULT_TEMPS,
        help="comma-separated softmax temperatures",
    )
    parser.add_argument("--dino", action="store_true", help="also sweep k in the DINOv2 space")
    run_sweep(parser.parse_args())


if __name__ == "__main__":
    main()
