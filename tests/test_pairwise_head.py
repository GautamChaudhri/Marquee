"""Pairwise learned-head tests (no ML extras required).

Exercises the v3 ranking-event → preference-pair expansion
(``build_pairwise_training_data``) and the RankNet head
(``LogisticHead.train_pairwise``): tie handling, per-movie + confidence
weighting, the feature intersection guard, and ordering recovery.
"""

from __future__ import annotations

import numpy as np
import pytest

from marquee.core.pipeline_config import pipeline_settings
from marquee.ml.head_trainer import build_pairwise_training_data, build_pointwise_pseudo_labels
from marquee.ml.learned_head import LogisticHead, fit_scale_bias


def _event(movie_id, favorites, hated, feats, **extra):
    """One v3 ranking row. ``feats`` maps orig_filename -> normalized dict."""
    return {
        "v": 3,
        "type": "ranking",
        "movie_id": movie_id,
        "favorites": favorites,
        "hated": hated,
        "candidates": [
            {"orig_filename": name, "normalized_features": vec} for name, vec in feats.items()
        ],
        **extra,
    }


def test_partial_order_expands_to_pairs_with_weights():
    w = pipeline_settings.FEEDBACK_INDIFF_HATE_PAIR_WEIGHT
    feats = {
        "a.jpg": {"x": 1.0, "y": 0.0},  # fav tier 1
        "b.jpg": {"x": 0.5, "y": 0.0},  # fav tier 2
        "c.jpg": {"x": 0.2, "y": 0.0},  # indifferent (untouched)
        "d.jpg": {"x": 0.0, "y": 0.0},  # hated
    }
    rows = [_event(1, [["a.jpg"], ["b.jpg"]], ["d.jpg"], feats)]

    diffs, weights, names, n_movies, n_pairs = build_pairwise_training_data(rows)

    # a≻b, a≻c, a≻d, b≻c, b≻d, c≻d  → 6 pairs, none within a tier.
    assert n_pairs == 6
    assert n_movies == 1
    assert names == ["x", "y"]
    # Explicit pairs (a≻b, a≻d, b≻d) weight 1/6; implicit (touching c) weight w/6.
    assert weights.sum() == pytest.approx((3 * 1.0 + 3 * w) / 6)
    # Every winner outscores its loser on feature x → all diffs[:, 0] > 0.
    assert np.all(diffs[:, 0] > 0)


def test_ties_within_a_tier_make_no_pair():
    feats = {
        "a.jpg": {"x": 1.0},
        "b.jpg": {"x": 0.9},  # co-favorite with a (same tier)
        "c.jpg": {"x": 0.1},  # indifferent
    }
    rows = [_event(1, [["a.jpg", "b.jpg"]], [], feats)]

    _, _, _, _, n_pairs = build_pairwise_training_data(rows)

    # a≻c and b≻c only — a and b are tied, so no a≻b pair.
    assert n_pairs == 2


def test_legacy_rows_contribute_no_pairs():
    rows = [
        {"v": 2, "label": 1, "title": "X", "normalized_features": {"x": 1.0}},
        {"v": 1, "label": 0, "title": "Y", "orig_filename": "z.jpg"},
    ]
    diffs, weights, names, n_movies, n_pairs = build_pairwise_training_data(rows)
    assert n_pairs == 0
    assert n_movies == 0
    assert diffs.size == 0


def test_feature_intersection_drops_uncommon_keys():
    feats = {
        "a.jpg": {"x": 1.0, "y": 5.0},  # favorite
        "b.jpg": {"x": 0.0},  # hated — missing "y"
    }
    rows = [_event(1, [["a.jpg"]], ["b.jpg"], feats)]
    _, _, names, _, n_pairs = build_pairwise_training_data(rows)
    assert n_pairs == 1
    assert names == ["x"]  # "y" not present on every paired candidate


def test_per_movie_normalization_balances_slate_size():
    # Movie 1: tiny slate (1 fav, 1 hate) → 1 pair. Movie 2: larger slate.
    rows = [
        _event(1, [["a.jpg"]], ["b.jpg"], {"a.jpg": {"x": 1.0}, "b.jpg": {"x": 0.0}}),
        _event(
            2,
            [["p.jpg"]],
            ["q.jpg", "r.jpg", "s.jpg"],
            {
                "p.jpg": {"x": 1.0},
                "q.jpg": {"x": 0.0},
                "r.jpg": {"x": 0.0},
                "s.jpg": {"x": 0.0},
            },
        ),
    ]
    _, weights, _, n_movies, _ = build_pairwise_training_data(rows)
    assert n_movies == 2
    # Each movie's pair weights sum to ~1 (all explicit here), so neither
    # movie dominates regardless of how many candidates it had.
    # Movie 1 has 1 pair (weight 1.0); movie 2 has 3 pairs (each 1/3).
    assert weights.sum() == pytest.approx(2.0)


def test_train_pairwise_recovers_ordering():
    # Winner always higher on feature 0, equal on feature 1.
    diffs = np.array([[1.0, 0.0], [2.0, 0.0], [0.5, 0.0], [1.5, 0.0]])
    weights = np.ones(len(diffs))
    head = LogisticHead.train_pairwise(diffs, weights, ["x", "y"], l2=0.01)

    assert head.bias == 0.0
    assert head.weights[0] > 0  # learned that higher x = preferred
    assert head.train_accuracy == 1.0  # all pairs ordered correctly
    # The inference scorer orders a high-x poster above a low-x one.
    hi, _ = head.score({"x": 1.0, "y": 0.0})
    lo, _ = head.score({"x": 0.0, "y": 0.0})
    assert hi > lo


def test_pointwise_pseudo_labels_use_favorites_and_hated_only():
    feats = {
        "a.jpg": {"x": 1.0, "y": 0.0},  # fav tier 1
        "b.jpg": {"x": 0.5, "y": 0.0},  # fav tier 2
        "c.jpg": {"x": 0.2, "y": 0.0},  # indifferent — must be skipped
        "d.jpg": {"x": 0.0, "y": 0.0},  # hated
    }
    rows = [_event(1, [["a.jpg"], ["b.jpg"]], ["d.jpg"], feats)]

    x, y, n_movies = build_pointwise_pseudo_labels(rows, ["x", "y"])

    assert n_movies == 1
    assert sorted(y.tolist()) == [0.0, 1.0, 1.0]  # a, b -> 1; d -> 0; c skipped
    assert x.shape == (3, 2)


def test_pointwise_pseudo_labels_require_every_requested_feature():
    feats = {
        "a.jpg": {"x": 1.0, "y": 5.0},  # favorite, has both features
        "b.jpg": {"x": 0.0},  # hated, missing "y" -> excluded
    }
    rows = [_event(1, [["a.jpg"]], ["b.jpg"], feats)]

    x, y, n_movies = build_pointwise_pseudo_labels(rows, ["x", "y"])

    assert n_movies == 1
    assert y.tolist() == [1.0]
    assert x.shape == (1, 2)


def test_fit_scale_bias_recovers_separable_labels():
    # Raw scores clearly separated by label -> calibration should produce a
    # positive scale and put the decision boundary near the midpoint.
    raw_scores = np.array([-6.0, -5.0, -4.5, 4.5, 5.0, 6.0])
    labels = np.array([0.0, 0.0, 0.0, 1.0, 1.0, 1.0])

    scale, bias = fit_scale_bias(raw_scores, labels)

    assert scale > 0
    probabilities = 1.0 / (1.0 + np.exp(-(scale * raw_scores + bias)))
    assert np.all(probabilities[labels == 1.0] > 0.5)
    assert np.all(probabilities[labels == 0.0] < 0.5)


def test_fit_scale_bias_degenerate_labels_do_not_invert_score():
    # Labels uncorrelated with (actually inverted vs.) the raw scores: the
    # fit may legitimately come back with scale <= 0 — callers must guard
    # against applying it, never assume a positive scale.
    raw_scores = np.array([-3.0, -2.0, -1.0, 1.0, 2.0, 3.0])
    labels = np.array([1.0, 1.0, 1.0, 0.0, 0.0, 0.0])

    scale, _bias = fit_scale_bias(raw_scores, labels)

    assert scale <= 0


def test_train_pairwise_then_calibrate_fixes_saturation_without_reordering():
    # Mirrors the live bug: large-magnitude pairwise weights (diffs are small
    # per pair, so the optimizer needs big coefficients to separate them)
    # saturate sigmoid(x.w + 0) for every candidate at ~0.999+.
    rng_diffs = np.array([[0.2], [0.15], [0.25], [0.1], [0.3]] * 4)
    weights = np.ones(len(rng_diffs))
    raw_head = LogisticHead.train_pairwise(rng_diffs, weights, ["knn_sim"], l2=0.01)
    assert raw_head.bias == 0.0
    assert abs(raw_head.weights[0]) > 5  # reproduces the live artifact's magnitude

    # Uncalibrated: a clearly-better and a clearly-worse candidate score much
    # closer together than after calibration — the saturation that, with the
    # full ~13-feature live artifact, compresses every candidate near 1.0.
    good_raw, _ = raw_head.score({"knn_sim": 0.9})
    bad_raw, _ = raw_head.score({"knn_sim": 0.1})
    uncalibrated_gap = good_raw - bad_raw

    favorites_data = {f"fav{i}.jpg": {"knn_sim": 0.85 + i * 0.01} for i in range(8)}
    hated_data = {f"hate{i}.jpg": {"knn_sim": 0.15 - i * 0.01} for i in range(8)}
    feats = {**favorites_data, **hated_data}
    rows = [_event(1, [list(favorites_data.keys())], list(hated_data.keys()), feats)]
    calib_x, calib_y, _ = build_pointwise_pseudo_labels(rows, raw_head.feature_names)
    scale, bias = fit_scale_bias(calib_x @ raw_head.weights, calib_y)
    assert scale > 0
    calibrated = raw_head.calibrated(scale, bias)

    good_score, _ = calibrated.score({"knn_sim": 0.9})
    bad_score, _ = calibrated.score({"knn_sim": 0.1})
    mid_score, _ = calibrated.score({"knn_sim": 0.5})

    # Calibration recovers a real spread instead of saturated near-1.0 ties.
    calibrated_gap = good_score - bad_score
    assert calibrated_gap > uncalibrated_gap
    assert calibrated_gap > 0.3
    assert bad_score < mid_score < good_score
    # Ordering from the original RankNet objective is preserved.
    assert good_score > bad_score
