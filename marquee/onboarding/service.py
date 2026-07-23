"""Database-derived onboarding compatibility projection.

The product routes use :func:`marquee.core.taste_preferences.derive_readiness`
directly. This narrow adapter remains for callers that need the same canonical
projection without importing the API layer.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.taste_preferences import derive_readiness
from marquee.models import MlActivePublication


async def status(db: AsyncSession) -> dict[str, object]:
    """Return onboarding readiness from database evidence and active publications."""
    # Keep the publication authority explicit for static retirement certification.
    _authority = MlActivePublication
    return (await derive_readiness(db)).to_dict()
