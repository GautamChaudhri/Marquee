"""Radarr API client."""

from __future__ import annotations

from .base_client import ArrClient


class RadarrClient(ArrClient):
    """HTTP client for Radarr's v3 API."""

    def __init__(self, base_url: str, api_key: str):
        super().__init__(base_url, api_key, service_name="Radarr")

    async def get_movies(self) -> list[dict]:
        """Fetch all movies in the Radarr library."""
        return await self._get("/api/v3/movie")

    async def get_custom_formats(self) -> list[dict]:
        """Fetch all custom format definitions."""
        return await self._get("/api/v3/customformat")

    async def get_quality_profiles(self) -> list[dict]:
        """Fetch all quality profile definitions."""
        return await self._get("/api/v3/qualityprofile")
