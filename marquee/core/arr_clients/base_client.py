"""Base HTTP client for Sonarr and Radarr APIs.

Shared error wrapping, connection lifecycle, and convenience methods.
"""

from __future__ import annotations

import httpx

from .exceptions import (
    ArrAuthenticationError,
    ArrConnectionError,
    ArrNotFoundError,
    ArrResponseError,
)


class ArrClient:
    """Base class for *arr API clients.

    Handles connection lifecycle, authentication headers, and unified
    error wrapping. Subclass for Radarr or Sonarr — just add endpoint methods.
    """

    def __init__(self, base_url: str, api_key: str, service_name: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.service_name = service_name
        self._client: httpx.AsyncClient | None = None

    # ── Lifecycle ────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Create the httpx client. Called once at app startup."""
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"X-Api-Key": self.api_key, "Accept": "application/json"},
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
            follow_redirects=False,
        )

    async def disconnect(self) -> None:
        """Close the httpx client. Called once at app shutdown."""
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── HTTP helpers ─────────────────────────────────────────────────

    async def _request(self, method: str, path: str, **kwargs) -> dict | list:
        """All endpoint methods go through here. Errors are wrapped."""
        try:
            response = await self._client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.ConnectError:
            raise ArrConnectionError(
                f"Cannot connect to {self.service_name} at {self.base_url}. Is the service running?"
            ) from None
        except httpx.TimeoutException:
            raise ArrConnectionError(f"{self.service_name} request timed out.") from None
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                raise ArrAuthenticationError(
                    f"{self.service_name} returned 401 — check your API key."
                ) from exc
            if status == 404:
                raise ArrNotFoundError(f"{self.service_name} resource not found: {path}") from exc
            raise ArrResponseError(
                f"{self.service_name} returned unexpected status {status} for {path}",
                status_code=status,
            ) from exc

    async def _get(self, path: str, **kwargs) -> dict | list:
        return await self._request("GET", path, **kwargs)

    async def _post(self, path: str, **kwargs) -> dict | list:
        return await self._request("POST", path, **kwargs)

    # ── Shared endpoints ─────────────────────────────────────────────

    async def get_system_status(self) -> dict:
        """Check if the service is reachable. Works on both Radarr and Sonarr."""
        return await self._get("/api/v3/system/status")
