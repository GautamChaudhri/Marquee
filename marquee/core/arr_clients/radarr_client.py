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

    async def get_movie_files(self, movie_ids: list[int], *, chunk_size: int = 100) -> list[dict]:
        """Fetch current movie-file payloads for one or more movie IDs.

        Radarr's bulk movie payload omits custom-format score truth on some
        installs, but ``/api/v3/moviefile`` returns the active file's
        ``customFormatScore`` and matched ``customFormats``. The endpoint
        accepts repeated ``movieId`` query params.
        """
        if not movie_ids:
            return []

        rows: list[dict] = []
        for start in range(0, len(movie_ids), chunk_size):
            chunk = movie_ids[start : start + chunk_size]
            payload = await self._get(
                "/api/v3/moviefile",
                params=[("movieId", movie_id) for movie_id in chunk],
            )
            if isinstance(payload, list):
                rows.extend(payload)
        return rows

    async def get_custom_formats(self) -> list[dict]:
        """Fetch all custom format definitions."""
        return await self._get("/api/v3/customformat")

    async def get_quality_profiles(self) -> list[dict]:
        """Fetch all quality profile definitions."""
        return await self._get("/api/v3/qualityprofile")
