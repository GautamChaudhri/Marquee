"""Pure official-pick promotion algorithm (design 04 §9.3, D8).

Migrated from the retired ``tests/test_batch_runner.py`` when the batch poster
engine was retired in JMC6F; the poster-ranking algorithm itself is preserved in
``marquee/pipeline/official_pick.py`` and exercised directly here.
"""

from __future__ import annotations

from pathlib import Path

from marquee.pipeline.official_pick import apply_official_pick
from marquee.pipeline.types import CandidateScore


def _stacked_candidate(
    name: str, *, stack_id: int, stack_rank: int, stack_size: int
) -> CandidateScore:
    return CandidateScore(
        image_path=Path(name),
        orig_filename=name,
        final_score=1.0 - stack_rank * 0.01,
        stack_id=stack_id,
        stack_rank=stack_rank,
        stack_pos=1,
        stack_label="A",
        stack_size=stack_size,
    )


def _three_stacks() -> list[CandidateScore]:
    # rank 1: single-member stack (best score); rank 2: 2-member stack
    # (primary lives here); rank 3: 3-member stack (largest).
    a = _stacked_candidate("a.jpg", stack_id=0, stack_rank=1, stack_size=1)
    b1 = _stacked_candidate("b1.jpg", stack_id=1, stack_rank=2, stack_size=2)
    b2 = CandidateScore(
        image_path=Path("b2.jpg"),
        orig_filename="b2.jpg",
        final_score=0.5,
        stack_id=1,
        stack_rank=2,
        stack_pos=2,
        stack_label="B",
        stack_size=2,
    )
    c1 = _stacked_candidate("c1.jpg", stack_id=2, stack_rank=3, stack_size=3)
    c2 = CandidateScore(
        image_path=Path("c2.jpg"),
        orig_filename="c2.jpg",
        final_score=0.4,
        stack_id=2,
        stack_rank=3,
        stack_pos=2,
        stack_label="B",
        stack_size=3,
    )
    c3 = CandidateScore(
        image_path=Path("c3.jpg"),
        orig_filename="c3.jpg",
        final_score=0.3,
        stack_id=2,
        stack_rank=3,
        stack_pos=3,
        stack_label="C",
        stack_size=3,
    )
    return [a, b1, b2, c1, c2, c3]


def test_official_pick_promotes_primary_stack_and_shifts_others():
    ranked = _three_stacks()
    result = apply_official_pick(ranked, "b1.jpg", enabled=True, fallback="ranked")

    assert result == {"enabled": True, "primary_name": "b1.jpg", "applied": "primary_stack"}
    by_name = {c.orig_filename: c for c in ranked}
    assert by_name["b1.jpg"].stack_rank == 1
    assert by_name["b2.jpg"].stack_rank == 1
    assert by_name["a.jpg"].stack_rank == 2  # was rank 1, shifted down
    assert by_name["c1.jpg"].stack_rank == 3  # already behind the primary — unchanged
    # stack_pos/stack_label (position within a stack) are untouched by promotion.
    assert by_name["b1.jpg"].stack_pos == 1
    assert by_name["b2.jpg"].stack_pos == 2


def test_official_pick_already_rank_one_is_noop():
    ranked = _three_stacks()
    result = apply_official_pick(ranked, "a.jpg", enabled=True, fallback="ranked")

    assert result == {"enabled": True, "primary_name": "a.jpg", "applied": "primary_stack"}
    assert {c.orig_filename: c.stack_rank for c in ranked} == {
        "a.jpg": 1,
        "b1.jpg": 2,
        "b2.jpg": 2,
        "c1.jpg": 3,
        "c2.jpg": 3,
        "c3.jpg": 3,
    }


def test_official_pick_primary_gated_falls_back_to_largest_stack():
    ranked = _three_stacks()
    result = apply_official_pick(ranked, "missing.jpg", enabled=True, fallback="largest_stack")

    assert result == {
        "enabled": True,
        "primary_name": "missing.jpg",
        "applied": "largest_stack",
        "fallback_used": True,
    }
    by_name = {c.orig_filename: c for c in ranked}
    # The 3-member stack (c) was the largest — promoted to rank 1.
    assert by_name["c1.jpg"].stack_rank == 1
    assert by_name["a.jpg"].stack_rank == 2
    assert by_name["b1.jpg"].stack_rank == 3


def test_official_pick_primary_gated_ranked_fallback_does_nothing():
    ranked = _three_stacks()
    original_ranks = {c.orig_filename: c.stack_rank for c in ranked}
    result = apply_official_pick(ranked, "missing.jpg", enabled=True, fallback="ranked")

    assert result == {
        "enabled": True,
        "primary_name": "missing.jpg",
        "applied": None,
        "fallback_used": True,
    }
    assert {c.orig_filename: c.stack_rank for c in ranked} == original_ranks


def test_official_pick_stacking_disabled_is_noop():
    # No assign_stacks() call means every stack_rank stays None.
    ranked = [
        CandidateScore(image_path=Path("a.jpg"), orig_filename="a.jpg", final_score=0.9),
        CandidateScore(image_path=Path("b.jpg"), orig_filename="b.jpg", final_score=0.5),
    ]
    result = apply_official_pick(ranked, "b.jpg", enabled=True, fallback="ranked")

    assert result == {
        "enabled": True,
        "primary_name": "b.jpg",
        "applied": None,
        "reason": "stacking_disabled",
    }


def test_official_pick_disabled_is_noop():
    ranked = _three_stacks()
    result = apply_official_pick(ranked, "b1.jpg", enabled=False, fallback="ranked")
    assert result == {"enabled": False, "primary_name": "b1.jpg", "applied": None}


def test_official_pick_empty_ranked_is_noop():
    result = apply_official_pick([], "a.jpg", enabled=True, fallback="ranked")
    assert result == {"enabled": True, "primary_name": "a.jpg", "applied": None}
