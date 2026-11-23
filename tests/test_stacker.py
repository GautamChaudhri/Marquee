"""Unit tests for the poster stack layer (``marquee/pipeline/stacker.py``).

Pure logic — no ML extras or model files. Embeddings are hand-built unit
vectors so cosine similarity is exact and the clustering is deterministic.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from marquee.pipeline.stacker import _pos_label, assign_stacks
from marquee.pipeline.types import CandidateScore


def _cfg(**overrides) -> SimpleNamespace:
    base = {
        "STACK_SIGNAL": "dino",
        "STACK_SIM_THRESHOLD": 0.9,
        "STACK_PHASH_MAX_DISTANCE": 12,
        "STACK_AGG_TOPK": 3,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _cand(name: str, score: float, vec=None) -> CandidateScore:
    c = CandidateScore(image_path=Path(name), orig_filename=name)
    c.final_score = score
    if vec is not None:
        c.embedding = np.asarray(vec, dtype=np.float32)
    return c


def test_pos_label_is_spreadsheet_style():
    assert [_pos_label(i) for i in (1, 2, 26, 27, 28)] == ["A", "B", "Z", "AA", "AB"]


def test_empty_input():
    assert assign_stacks([], config=_cfg()) == []


def test_groups_same_design_and_ranks_by_topk_mean():
    # Design A: three tight variants (top-3 mean = 0.797).
    # Design B: holds the single global max 0.83 but a weak 0.70 → top-2 mean
    #   0.765, so it ranks *below* the more consistent design A.
    # One unrelated loner at 0.60.
    design_a = [
        _cand("a0", 0.80, [1, 0, 0.02]),
        _cand("a1", 0.78, [1, 0.02, 0]),
        _cand("a2", 0.81, [0.99, 0, 0.03]),
    ]
    design_b = [_cand("b0", 0.83, [0, 1, 0]), _cand("b1", 0.70, [0, 0.99, 0.02])]
    lone = [_cand("lone", 0.60, [0, 0, 1])]
    ranked = design_a + design_b + lone

    stacks = assign_stacks(ranked, config=_cfg())

    assert [s.stack_rank for s in stacks] == [1, 2, 3]
    assert {m.orig_filename for m in stacks[0].members} == {"a0", "a1", "a2"}
    assert {m.orig_filename for m in stacks[1].members} == {"b0", "b1"}
    assert stacks[0].stack_score > stacks[1].stack_score
    # Within design A, members are ordered by individual score → A, B, C.
    assert [(m.orig_filename, m.stack_label) for m in stacks[0].members] == [
        ("a2", "A"),
        ("a0", "B"),
        ("a1", "C"),
    ]
    # The auto-pick (1A) is a2 even though b0 has the higher *single* score —
    # the robust top-K mean rewards the consistently strong design.
    auto = next(c for c in ranked if c.stack_rank == 1 and c.stack_pos == 1)
    assert auto.orig_filename == "a2"


def test_topk_one_reduces_to_max():
    design_a = [_cand("a0", 0.50, [1, 0, 0]), _cand("a1", 0.55, [1, 0.01, 0])]
    design_b = [_cand("b0", 0.52, [0, 1, 0])]
    stacks = assign_stacks(design_a + design_b, config=_cfg(STACK_AGG_TOPK=1))
    # K=1 → stack score = best member, so design A (max 0.55) beats B (0.52).
    assert stacks[0].members[0].orig_filename == "a1"
    assert stacks[0].stack_score == pytest.approx(0.55)


def test_tight_threshold_splits_borderline_variants():
    # cos([1,0,0], [0.9,0.43,0]) ≈ 0.90 — merges at 0.9, splits at 0.99.
    pair = [_cand("a0", 0.8, [1, 0, 0]), _cand("a1", 0.7, [0.9, 0.43, 0])]
    assert len(assign_stacks(pair, config=_cfg(STACK_SIM_THRESHOLD=0.99))) == 2
    pair2 = [_cand("a0", 0.8, [1, 0, 0]), _cand("a1", 0.7, [0.9, 0.43, 0])]
    assert len(assign_stacks(pair2, config=_cfg(STACK_SIM_THRESHOLD=0.85))) == 1


def test_degrades_to_singletons_without_vectors():
    ranked = [_cand("x", 0.4), _cand("y", 0.9), _cand("z", 0.6)]  # no embeddings
    stacks = assign_stacks(ranked, config=_cfg())
    assert len(stacks) == 3
    # Singleton stacks fall back to ranking purely by score.
    assert [s.members[0].orig_filename for s in stacks] == ["y", "z", "x"]
    assert all(m.stack_label == "A" and m.stack_size == 1 for s in stacks for m in s.members)


def test_phash_signal_groups_by_hamming_distance():
    imagehash = pytest.importorskip("imagehash")
    near = [_cand("p0", 0.8), _cand("p1", 0.7), _cand("p2", 0.6)]
    near[0].embedding = imagehash.hex_to_hash("ffffffffffffffff")
    near[1].embedding = imagehash.hex_to_hash("ffffffffffffff0f")  # ~4 bits off
    near[2].embedding = imagehash.hex_to_hash("0000000000000000")  # 64 bits off
    stacks = assign_stacks(near, config=_cfg(STACK_SIGNAL="phash", STACK_PHASH_MAX_DISTANCE=12))
    # p0+p1 collapse into one design; p2 stands alone.
    assert sorted(len(s.members) for s in stacks) == [1, 2]
