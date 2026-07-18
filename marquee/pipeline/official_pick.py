"""Pure TMDB official-pick promotion (design 04 §9.3, decision D8).

Extracted from the retired batch poster engine so the poster-ranking algorithm
survives behind a neutral module with no job/run/batch lifecycle. Given a ranked
candidate list and the TMDB primary poster filename, promote the primary
poster's stack to rank 1 so ``find_auto_pick_candidate`` (``stack_rank == 1 and
stack_pos == 1``) selects it — no other code path needs to change.
"""

from __future__ import annotations

from marquee.pipeline.types import CandidateScore


def apply_official_pick(
    ranked: list[CandidateScore],
    primary_name: str | None,
    *,
    enabled: bool,
    fallback: str,
) -> dict[str, object]:
    """Post-stacking promotion: bump the TMDB primary poster's stack to rank 1.

    Call after ``assign_stacks()`` and before the archive/output are written.
    Mutates ``stack_rank`` on every affected member so
    ``find_auto_pick_candidate`` picks up the promotion automatically.
    """
    if not enabled or primary_name is None or not ranked:
        return {"enabled": enabled, "primary_name": primary_name, "applied": None}
    if ranked[0].stack_rank is None:
        # STACK_ENABLED=False — no stack metadata to promote.
        return {
            "enabled": enabled,
            "primary_name": primary_name,
            "applied": None,
            "reason": "stacking_disabled",
        }

    def _promote(target_rank: int) -> None:
        for candidate in ranked:
            if candidate.stack_rank < target_rank:
                candidate.stack_rank += 1
            elif candidate.stack_rank == target_rank:
                candidate.stack_rank = 1

    target = next((c for c in ranked if c.orig_filename == primary_name), None)
    if target is not None:
        if target.stack_rank != 1:
            _promote(target.stack_rank)
        return {"enabled": enabled, "primary_name": primary_name, "applied": "primary_stack"}

    if fallback == "largest_stack":
        stack_sizes: dict[int, int] = {}
        for candidate in ranked:
            stack_sizes.setdefault(candidate.stack_rank, candidate.stack_size or 1)
        largest_rank = min(stack_sizes, key=lambda rank: (-stack_sizes[rank], rank))
        if largest_rank != 1:
            _promote(largest_rank)
        return {
            "enabled": enabled,
            "primary_name": primary_name,
            "applied": "largest_stack",
            "fallback_used": True,
        }

    return {
        "enabled": enabled,
        "primary_name": primary_name,
        "applied": None,
        "fallback_used": True,
    }
