"""Canonical JMC4C poster analysis handler registration."""

from __future__ import annotations

from marquee.core.jobs.delivery import ExecutionContext, register_execution_handler
from marquee.core.jobs.documents import PosterPipelineRequestV1
from marquee.core.jobs.poster_pipeline import execute_poster_pipeline


async def execute_poster_analysis(context: ExecutionContext) -> dict[str, object]:
    """Run immutable candidate analysis; this handler has no artwork deployment authority."""
    request = PosterPipelineRequestV1.model_validate(context.request)
    return await execute_poster_pipeline(context, request)


register_execution_handler("poster_pipeline", execute_poster_analysis)
