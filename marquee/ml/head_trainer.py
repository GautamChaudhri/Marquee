"""Train the Phase-1 learned head from pipeline runs + a labels file.

The training data is what the pipeline already records: every
``pipeline_run.json`` carries the full normalized feature vector for every
candidate. Labels come from a JSONL file that the future feedback UI will
write (and which can be hand-edited until then):

    marquee/experiments/feedback/labels.jsonl

One JSON object per line:

    {"title": "2001: A Space Odyssey", "orig_filename": "b51Qn...jpg", "label": 1}
    {"title": "2001: A Space Odyssey", "orig_filename": "8bvDm...jpg", "label": 0}

Semantics per design 04 §5: 1 = you approved/selected this poster;
0 = the auto-pick you overrode (or any poster you explicitly rejected).
Unlabeled candidates are not trained on.

Usage:

    python -m marquee.ml.head_trainer            # train + save if enough labels
    python -m marquee.ml.head_trainer --min-labels 10   # lower the safety floor

Once the artifact exists, SCORER=auto switches the pipeline to the learned
head automatically (logged at rank time).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from marquee.core.pipeline_config import pipeline_settings
from marquee.ml.learned_head import LogisticHead

_RUNS_DIR = Path(__file__).resolve().parents[1] / "experiments" / "runs"
_LABELS_PATH = (
    Path(__file__).resolve().parents[1] / "experiments" / "feedback" / "labels.jsonl"
)
# Below this many labels a learned head is worse than the hand weights
# (design 04 §5 suggests 50-100 approvals before Phase 1).
_DEFAULT_MIN_LABELS = 30


def load_labels(path: Path) -> dict[tuple[str, str], int]:
    labels: dict[tuple[str, str], int] = {}
    if not path.exists():
        return labels
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            labels[(entry["title"], entry["orig_filename"])] = int(entry["label"])
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            print(f"[WARN] labels.jsonl line {line_number} skipped: {exc}")
    return labels


def collect_samples(
    runs_dir: Path,
    labels: dict[tuple[str, str], int],
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Join labels against the recorded normalized features of every run."""
    rows: list[dict[str, float]] = []
    targets: list[int] = []
    for run_json in sorted(runs_dir.glob("*/pipeline_run.json")):
        try:
            payload = json.loads(run_json.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[WARN] Skipping unreadable {run_json}: {exc}")
            continue
        title = payload.get("title", "")
        if payload.get("model_name") != pipeline_settings.AI_MODEL:
            print(f"[WARN] Skipping {run_json} (model {payload.get('model_name')!r})")
            continue
        for candidate in payload.get("candidates", []):
            key = (title, candidate.get("orig_filename", ""))
            if key not in labels:
                continue
            normalized = candidate.get("normalized_features")
            if not normalized:
                print(f"[WARN] Labeled candidate without features: {key}")
                continue
            rows.append(normalized)
            targets.append(labels[key])

    if not rows:
        return np.empty((0, 0)), np.empty(0), []

    # Use the features present in EVERY sample, so heads never train on
    # values that exist for some runs but not others (e.g. dino on/off).
    common = sorted(set.intersection(*(set(row) for row in rows)))
    matrix = np.asarray(
        [[row[name] for name in common] for row in rows], dtype=np.float64
    )
    return matrix, np.asarray(targets, dtype=np.float64), common


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, default=_RUNS_DIR)
    parser.add_argument("--labels", type=Path, default=_LABELS_PATH)
    parser.add_argument("--output", type=Path, default=pipeline_settings.LEARNED_HEAD_PATH)
    parser.add_argument("--min-labels", type=int, default=_DEFAULT_MIN_LABELS)
    parser.add_argument("--l2", type=float, default=1.0)
    args = parser.parse_args()

    labels = load_labels(args.labels)
    print(f"[INFO] {len(labels)} labels loaded from {args.labels}")
    features, targets, names = collect_samples(args.runs_dir, labels)
    print(f"[INFO] {len(targets)} labeled samples matched against runs")

    if len(targets) < args.min_labels:
        raise SystemExit(
            f"[ABORT] Only {len(targets)} labeled samples (< {args.min_labels}). "
            "The hand-weighted scorer outperforms a head trained on this little "
            "data — collect more approvals/overrides first, or pass --min-labels."
        )

    positives = int(targets.sum())
    print(f"[INFO] Training on {len(targets)} samples ({positives} positive) "
          f"x {len(names)} features: {names}")
    head = LogisticHead.train(features, targets, names, l2=args.l2)
    print(f"[INFO] Train accuracy: {head.train_accuracy:.3f}")
    print("[INFO] Learned weights (your taste, read out):")
    for name, weight in sorted(
        zip(head.feature_names, head.weights, strict=True),
        key=lambda item: -abs(item[1]),
    ):
        print(f"  {name:>22s}: {weight:+.4f}")
    head.save(args.output)
    print(f"[INFO] SCORER=auto will now use the learned head ({args.output.name})")


if __name__ == "__main__":
    main()
