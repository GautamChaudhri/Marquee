"""JMC6J compatibility helpers for assertions based on historical freeze fixtures."""

from collections.abc import Iterable


def current_job_types(values: Iterable[str]) -> set[str]:
    """Project a pre-JMC6J job set onto the unreleased current product surface."""
    result = set(values)
    if "learned_head_train" in result:
        result.remove("learned_head_train")
        result.add("ranking_residual_train")
    return result
