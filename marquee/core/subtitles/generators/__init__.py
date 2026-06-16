"""Subtitle generation providers (design §18.3, §24).

A small provider protocol so Marquee can drive Subgen today and other
OpenAI-compatible / local generators later without API or schema churn. The
provider runs as a *separate* service Marquee calls over HTTP — it is never
bundled into Marquee's container.
"""

from marquee.core.subtitles.generators.base import (
    GenerationRequest,
    GeneratorCapabilities,
    GeneratorHealth,
    ProviderState,
    ProviderSubmission,
    SubtitleGenerator,
)
from marquee.core.subtitles.generators.subgen import SubgenPathGenerator

__all__ = [
    "GenerationRequest",
    "GeneratorCapabilities",
    "GeneratorHealth",
    "ProviderState",
    "ProviderSubmission",
    "SubtitleGenerator",
    "SubgenPathGenerator",
]
