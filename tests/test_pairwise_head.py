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
from marquee.ml.head_trainer import build_pairwise_training_data
from marquee.ml.learned_head import LogisticHead


def _event(movie_id, favorites, hated, feats, **extra):
    """One v3 ranking row. ``feats`` maps orig_filename -> normalized dict."""
    return {
        "v": 3,
        "type": "ranking",
        "movie_id": movie_id,
        "favorites": favorites,
        "hated": hated,
        "candidates": [
            {"orig_filename": name, "normalized_features": vec}
            for name, vec in feats.items()
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
