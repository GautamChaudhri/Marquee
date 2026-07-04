"""Sonarr API client."""

from __future__ import annotations

from .base_client import ArrClient


class SonarrClient(ArrClient):
    """HTTP client for Sonarr's v3 API."""

    def __init__(self, base_url: str, api_key: str):
        super().__init__(base_url, api_key, service_name="Sonarr")

    async def get_series(self) -> list[dict]:
        """Fetch all TV series in the Sonarr library."""
        return await self._get("/api/v3/series")

    async def get_episodes(self, series_id: int) -> list[dict]:
        """Fetch all episodes for a given series."""
        return await self._get("/api/v3/episode", params={"seriesId": series_id})

    async def get_episode_files(self, series_id: int) -> list[dict]:
        """Fetch all episode file records for a given series."""
        return await self._get("/api/v3/episodefile", params={"seriesId": series_id})

    async def get_custom_formats(self) -> list[dict]:
        """Fetch all custom format definitions."""
        return await self._get("/api/v3/customformat")

    async def get_quality_profiles(self) -> list[dict]:
        """Fetch all quality profile definitions."""
        return await self._get("/api/v3/qualityprofile")
