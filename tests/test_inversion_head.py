"""Inversion-based learned-head tests (no ML extras required).

Exercises the v4 ranking-event -> preference-pair expansion
(``build_inversion_training_data``) and the RankNet head
(``LogisticHead.train_pairwise``): rank-inversion detection, hate-pile
pairing, position-discount weighting, per-movie normalization, the feature
intersection guard, and ordering recovery. See design/30 for the mechanism
this replaces (tier/bucket pairwise pairing) and why.
"""

from __future__ import annotations

import numpy as np
import pytest

from marquee.core.pipeline_config import pipeline_settings
from marquee.ml.feedback_store import positive_exemplar_count
from marquee.ml.head_trainer import (
    _pair_magnitude,
    _position_discount,
    build_inversion_training_data,
    build_pointwise_pseudo_labels,
)
from marquee.ml.learned_head import LogisticHead, fit_scale_bias


def _cand(name, baseline_rank, feats, **extra):
    return {
        "orig_filename": name,
        "baseline_rank": baseline_rank,
        "normalized_features": feats,
        **extra,
    }


def _event(movie_id, order, hated, **extra):
    """One v4 ranking row. ``order``/``hated`` are lists of candidate dicts
    (build with ``_cand``); ``order`` is final order, best -> worst."""
    return {
        "v": 4,
        "type": "ranking",
        "movie_id": movie_id,
        "order": order,
        "hated": hated,
        **extra,
    }


# ---------------------------------------------------------------------------
# Rank-inversion detection — the worked examples from design/30
# ---------------------------------------------------------------------------


def test_untouched_relative_order_produces_no_pair():
    order = [
        _cand("a.jpg", 1, {"x": 1.0}),
        _cand("b.jpg", 2, {"x": 0.5}),
        _cand("c.jpg", 3, {"x": 0.1}),
    ]
    rows = [_event(1, order, [])]

    _, _, _, n_movies, n_pairs = build_inversion_training_data(rows)

    assert n_pairs == 0
    assert n_movies == 0  # a movie that contributes nothing isn't counted


def test_adjacent_swap_produces_exactly_one_inverted_pair():
    # Baseline ranks 1..8; final order swaps the items at ranks 7 and 8 only.
    base = [_cand(f"p{i}.jpg", i, {"x": float(i)}) for i in range(1, 9)]
    final = base[:6] + [base[7], base[6]]
    rows = [_event(1, final, [])]

    diffs, weights, names, n_movies, n_pairs = build_inversion_training_data(rows)

    assert n_pairs == 1
    assert n_movies == 1
    # The winner is whichever poster ended up first in final order (p8).
    assert diffs[0, 0] == pytest.approx(8.0 - 7.0)


def test_promote_to_top_produces_inversions_against_everything_it_now_precedes():
    # Baseline ranks 1..9; final order moves the rank-9 item to position 2.
    base = [_cand(f"q{i}.jpg", i, {"x": float(i)}) for i in range(1, 10)]
    final = [base[0], base[8]] + base[1:8]  # q1, q9, q2..q8
    rows = [_event(1, final, [])]

    _, _, _, n_movies, n_pairs = build_inversion_training_data(rows)

    # q9 vs q2..q8 = 7 pairs. NOT vs q1 (q9 never passed it), and q2..q8 keep
    # their mutual order untouched, so the cascade needs no separate handling.
    assert n_pairs == 7
    assert n_movies == 1


# ---------------------------------------------------------------------------
# Hate-pile pairing
# ---------------------------------------------------------------------------


def test_hate_pile_pairs_only_against_baseline_better_survivors():
    # Hated poster's baseline_rank is 3; survivors at baseline 1, 2, 5, 6.
    survivors = [
        _cand("s1.jpg", 1, {"x": 1.0}),
        _cand("s2.jpg", 2, {"x": 2.0}),
        _cand("s5.jpg", 5, {"x": 5.0}),
        _cand("s6.jpg", 6, {"x": 6.0}),
    ]
    hated = [_cand("h.jpg", 3, {"x": 3.0})]
    rows = [_event(1, survivors, hated)]

    _, _, _, _, n_pairs = build_inversion_training_data(rows)

    # Only s5, s6 (baseline ranked them worse than the hated poster) — s1, s2
    # were already baseline-better, so hate adds no contradiction there.
    assert n_pairs == 2


def test_hate_pile_pair_weight_uses_indiff_hate_pair_weight_confidence():
    implicit = pipeline_settings.FEEDBACK_INDIFF_HATE_PAIR_WEIGHT
    a = _cand("a.jpg", 1, {"x": 1.0})  # baseline better (1)
    b = _cand("b.jpg", 2, {"x": 0.0})  # baseline worse (2)
    order = [b, a]  # final: b=rank1, a=rank2 — inversion (baseline preferred a)
    h = _cand("h.jpg", 0, {"x": -1.0})  # baseline best of all -> hated
    rows = [_event(1, order, [h])]

    diffs, weights, names, n_movies, n_pairs = build_inversion_training_data(rows)

    assert n_pairs == 3  # inversion(b,a) + hate(b,h) + hate(a,h)

    # Recompute the expected weights from the documented formula directly
    # (same helpers the implementation uses) rather than forcing magnitudes
    # to coincide, which the virtual hate-rank makes awkward to engineer.
    inv_magnitude = _pair_magnitude(1, 2)  # b (final 1) vs a (final 2)
    hate_b_magnitude = _pair_magnitude(1, 3)  # b (final 1) vs h (virtual rank 3)
    hate_a_magnitude = _pair_magnitude(2, 3)  # a (final 2) vs h (virtual rank 3)
    total_raw = inv_magnitude * 1.0 + hate_b_magnitude * implicit + hate_a_magnitude * implicit
    expected = sorted(
        [
            inv_magnitude * 1.0 / total_raw,
            hate_b_magnitude * implicit / total_raw,
            hate_a_magnitude * implicit / total_raw,
        ]
    )
    assert sorted(weights.tolist()) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Position-discount weighting
# ---------------------------------------------------------------------------


def test_position_discount_decreases_with_rank():
    assert _position_discount(1) > _position_discount(2) > _position_discount(10)


def test_pair_magnitude_bigger_jump_outweighs_small_swap():
    # One of the rank-9->2 jump's pairs vs. the rank-7<->8 swap's pair — the
    # dramatic promotion should weigh more, which is the whole point of
    # discounting by final position instead of treating every inversion as
    # equally important (see design/30).
    swap = _pair_magnitude(7, 8)
    jump = _pair_magnitude(2, 9)
    assert jump > swap


def test_movie_normalization_uses_magnitude_weighted_total_not_pair_count():
    # Movie 1: one dramatic promotion (7 high-magnitude pairs).
    base9 = [_cand(f"q{i}.jpg", i, {"x": float(i)}) for i in range(1, 10)]
    final9 = [base9[0], base9[8]] + base9[1:8]
    # Movie 2: one adjacent swap (1 low-magnitude pair).
    base8 = [_cand(f"p{i}.jpg", i, {"x": float(i)}) for i in range(1, 9)]
    final8 = base8[:6] + [base8[7], base8[6]]

    rows = [_event(1, final9, []), _event(2, final8, [])]
    diffs, weights, names, n_movies, n_pairs = build_inversion_training_data(rows)

    assert n_movies == 2
    assert n_pairs == 8  # 7 + 1

    # Every movie's pairs sum to the same total weight budget (1.0) whether
    # it has 7 pairs or 1 — the original purpose of movie normalization (no
    # movie dominates by pair count / slate size).
    movie1_weights = weights[:7]
    movie2_weight = weights[7]
    assert movie1_weights.sum() == pytest.approx(1.0)
    assert movie2_weight == pytest.approx(1.0)
    # But magnitude still differentiates WITHIN a movie's own budget: movie
    # 2's lone pair gets the *whole* budget, more than any single one of
    # movie 1's 7 pairs gets of its (also size-1.0) budget.
    assert movie2_weight > movie1_weights.max()


# ---------------------------------------------------------------------------
# Feature intersection / cutover (legacy rows ignored)
# ---------------------------------------------------------------------------


def test_feature_intersection_drops_uncommon_keys():
    a = _cand("a.jpg", 2, {"x": 1.0, "y": 5.0})
    b = _cand("b.jpg", 1, {"x": 0.0})  # missing "y"
    order = [a, b]  # final: a=rank1, b=rank2; baseline preferred b -> inversion
    rows = [_event(1, order, [])]

    _, _, names, _, n_pairs = build_inversion_training_data(rows)

    assert n_pairs == 1
    assert names == ["x"]  # "y" not present on every paired candidate


def test_legacy_rows_contribute_no_pairs():
    rows = [
        {"v": 2, "label": 1, "title": "X", "normalized_features": {"x": 1.0}},
        {"v": 1, "label": 0, "title": "Y", "orig_filename": "z.jpg"},
        # Pre-cutover v3 ranking row — structurally distinct, must be ignored
        # entirely (abandon, don't migrate — design/30).
        {
            "v": 3,
            "type": "ranking",
            "movie_id": 9,
            "favorites": [["a.jpg"]],
            "hated": ["b.jpg"],
            "candidates": [
                {"orig_filename": "a.jpg", "normalized_features": {"x": 1.0}},
                {"orig_filename": "b.jpg", "normalized_features": {"x": 0.0}},
            ],
        },
    ]

    diffs, weights, names, n_movies, n_pairs = build_inversion_training_data(rows)

    assert n_pairs == 0
    assert n_movies == 0
    assert diffs.size == 0


# ---------------------------------------------------------------------------
# RankNet head — unchanged mechanism, agnostic to how pairs were derived
# ---------------------------------------------------------------------------


def test_train_pairwise_recovers_ordering():
    diffs = np.array([[1.0, 0.0], [2.0, 0.0], [0.5, 0.0], [1.5, 0.0]])
    weights = np.ones(len(diffs))
    head = LogisticHead.train_pairwise(diffs, weights, ["x", "y"], l2=0.01)

    assert head.bias == 0.0
    assert head.weights[0] > 0
    assert head.train_accuracy == 1.0
    hi, _ = head.score({"x": 1.0, "y": 0.0})
    lo, _ = head.score({"x": 0.0, "y": 0.0})
    assert hi > lo


# ---------------------------------------------------------------------------
# Pointwise pseudo-labels (Platt calibration source)
# ---------------------------------------------------------------------------


def test_pointwise_pseudo_labels_use_top_slice_and_hated_only():
    order = [
        _cand("a.jpg", 1, {"x": 1.0}),
        _cand("b.jpg", 2, {"x": 0.5}),
        _cand("c.jpg", 3, {"x": 0.3}),
        _cand("d.jpg", 4, {"x": 0.2}),
        _cand("e.jpg", 5, {"x": 0.1}),
    ]
    hated = [_cand("h.jpg", 6, {"x": 0.0})]
    rows = [_event(1, order, hated)]

    x, y, n_movies = build_pointwise_pseudo_labels(rows, ["x"])

    n_pos = positive_exemplar_count(len(order))  # min(3, ceil(0.2*5)) = 1
    assert n_movies == 1
    assert sorted(y.tolist()) == sorted([1.0] * n_pos + [0.0])
    assert x.shape == (n_pos + 1, 1)


def test_pointwise_pseudo_labels_require_every_requested_feature():
    order = [_cand("a.jpg", 1, {"x": 1.0, "y": 5.0})]
    hated = [_cand("b.jpg", 2, {"x": 0.0})]  # missing "y" -> excluded
    rows = [_event(1, order, hated)]

    x, y, n_movies = build_pointwise_pseudo_labels(rows, ["x", "y"])

    assert n_movies == 1
    assert y.tolist() == [1.0]
    assert x.shape == (1, 2)


# ---------------------------------------------------------------------------
# Calibration — operates on raw score arrays / LogisticHead.calibrated(),
# agnostic to how diffs/weights were derived; unaffected by this redesign.
# ---------------------------------------------------------------------------


def test_fit_scale_bias_recovers_separable_labels():
    raw_scores = np.array([-6.0, -5.0, -4.5, 4.5, 5.0, 6.0])
    labels = np.array([0.0, 0.0, 0.0, 1.0, 1.0, 1.0])

    scale, bias = fit_scale_bias(raw_scores, labels)

    assert scale > 0
    probabilities = 1.0 / (1.0 + np.exp(-(scale * raw_scores + bias)))
    assert np.all(probabilities[labels == 1.0] > 0.5)
    assert np.all(probabilities[labels == 0.0] < 0.5)


def test_fit_scale_bias_degenerate_labels_do_not_invert_score():
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

    good_raw, _ = raw_head.score({"knn_sim": 0.9})
    bad_raw, _ = raw_head.score({"knn_sim": 0.1})
    uncalibrated_gap = good_raw - bad_raw

    # Final order, best -> worst, descending knn_sim.
    order = [
        _cand(f"fav{rank}.jpg", rank, {"knn_sim": score})
        for rank, score in enumerate([0.92, 0.91, 0.90, 0.89, 0.88, 0.87, 0.86, 0.85], start=1)
    ]
    hated = [_cand(f"hate{i}.jpg", 100 + i, {"knn_sim": 0.15 - i * 0.01}) for i in range(8)]
    rows = [_event(1, order, hated)]
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
