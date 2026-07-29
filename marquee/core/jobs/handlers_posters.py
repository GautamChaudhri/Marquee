"""Canonical JMC4C poster analysis handler registration."""

from __future__ import annotations

from marquee.core.jobs.delivery import ExecutionContext, register_execution_handler
from marquee.core.jobs.documents import PosterPipelineGroupRequestV1, PosterPipelineRequestV1
from marquee.core.jobs.poster_pipeline import execute_poster_pipeline
from marquee.core.jobs.poster_pipeline_group import execute_poster_pipeline_group


async def execute_poster_analysis(context: ExecutionContext) -> dict[str, object]:
    """Run immutable candidate analysis; this handler has no artwork deployment authority."""
    request = PosterPipelineRequestV1.model_validate(context.request)
    return await execute_poster_pipeline(context, request)


async def execute_poster_group_analysis(context: ExecutionContext) -> dict[str, object]:
    """Run one bounded stage-major group without artwork deployment authority."""
    request = PosterPipelineGroupRequestV1.model_validate(context.request)
    return await execute_poster_pipeline_group(context, request)


register_execution_handler("poster_pipeline", execute_poster_analysis)
register_execution_handler("poster_pipeline_group", execute_poster_group_analysis)
