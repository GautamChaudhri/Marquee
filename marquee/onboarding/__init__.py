"""Database-backed onboarding compatibility projection.

The product route owns neutral candidate review and explicit preference evidence.
This package preserves the lightweight readiness adapter for callers that do not
import the API layer directly.
"""

from marquee.onboarding import service

__all__ = ["service"]
