"""The Movie Database (TMDB) API client.

Authentication: Bearer token (Read Access Token) in the Authorization header.
Base URL: https://api.themoviedb.org/3
Image CDN: https://image.tmdb.org/t/p/{size}{file_path}
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from .exceptions import (
    TMDBAuthenticationError,
    TMDBConnectionError,
    TMDBNotFoundError,
    TMDBResponseError,
)

IMAGE_BASE_URL = "https://image.tmdb.org/t/p"


# ---------------------------------------------------------------------------
# PosterCandidate
# ---------------------------------------------------------------------------


@dataclass
class PosterCandidate:
    """A single poster option returned by TMDB images endpoints."""

    file_path: str  # e.g. "/gnb54uIjX2M81c6RWPM4uQwulMt.jpg"
    width: int
    height: int
    aspect_ratio: float
    language: str | None  # iso_639_1 — None means language-neutral (text-free)
    vote_average: float
    vote_count: int

    def url(self, size: str = "original") -> str:
        """Build the full CDN URL for this poster at the given size."""
        return f"{IMAGE_BASE_URL}/{size}{self.file_path}"


@dataclass
class MovieDetails:
    """TMDB movie metadata used to classify poster text."""

    director: str | None = None
    production_companies: list[str] = field(default_factory=list)
    tagline: str | None = None


@dataclass
class TVDetails:
    """TMDB TV show metadata used to classify poster text."""

    director: str | None = None
    production_companies: list[str] = field(default_factory=list)
    tagline: str | None = None


# ---------------------------------------------------------------------------
# TMDBClient
# ---------------------------------------------------------------------------


class TMDBClient:
    """Async HTTP client for TMDB API v3.

    Usage:
        client = TMDBClient(read_access_token="...")
        await client.connect()
        posters = await client.get_movie_images(550)
        await client.disconnect()
    """

    def __init__(
        self,
        read_access_token: str,
        base_url: str = "https://api.themoviedb.org/3",
    ):
        self.base_url = base_url
        self._token = read_access_token
        self._client: httpx.AsyncClient | None = None

    # ── Lifecycle ────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Create the httpx client. Called once at app startup."""
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
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
            raise TMDBConnectionError("Cannot connect to TMDB.") from None
        except httpx.TimeoutException:
            raise TMDBConnectionError("TMDB request timed out.") from None
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                raise TMDBAuthenticationError(
                    "TMDB authentication failed — check your Read Access Token."
                ) from exc
            if status == 404:
                raise TMDBNotFoundError(f"TMDB resource not found: {path}") from exc
            raise TMDBResponseError(
                f"TMDB returned unexpected status {status} for {path}",
                status_code=status,
            ) from exc

    async def _get(self, path: str, **kwargs) -> dict | list:
        return await self._request("GET", path, **kwargs)

    # ── Helpers ──────────────────────────────────────────────────────

    def _parse_posters(self, data: dict) -> list[PosterCandidate]:
        """Parse poster candidates from a TMDB images response dict."""
        posters = [
            PosterCandidate(
                file_path=p["file_path"],
                width=p["width"],
                height=p["height"],
                aspect_ratio=p["aspect_ratio"],
                language=p.get("iso_639_1"),
                vote_average=p.get("vote_average", 0.0),
                vote_count=p.get("vote_count", 0),
            )
            for p in data.get("posters", [])
        ]
        # Best-rated first; vote_count as tiebreaker
        posters.sort(key=lambda p: (p.vote_average, p.vote_count), reverse=True)
        return posters

    # ── Movie Endpoints ──────────────────────────────────────────────

    async def get_movie_images(
        self,
        tmdb_id: int,
        include_language: str = "en,null",
    ) -> list[PosterCandidate]:
        """Fetch all poster candidates for a movie.

        Args:
            tmdb_id: TMDB numeric movie ID.
            include_language: Comma-separated ISO 639-1 codes.
                ``"null"`` captures language-neutral (text-free) posters.
                Defaults to ``"en,null"`` — English + untagged.

        Returns:
            List of PosterCandidate, sorted by vote_average descending.
        """
        data = await self._get(
            f"/movie/{tmdb_id}/images",
            params={"include_image_language": include_language},
        )
        return self._parse_posters(data)

    async def get_movie_primary_poster(self, tmdb_id: int) -> str | None:
        """Return the filename of the movie's TMDB primary poster.

        The primary poster (the movie detail's ``poster_path``) is the
        community-selected representative image — in practice almost always
        the official key art. Used as the anchor for the pipeline's
        ``official_family`` signal.
        """
        data = await self._get(f"/movie/{tmdb_id}")
        poster_path = data.get("poster_path") if isinstance(data, dict) else None
        if not poster_path:
            return None
        return poster_path.lstrip("/").split("/")[-1]

    async def get_movie_details(self, tmdb_id: int) -> MovieDetails:
        """Fetch movie metadata used by the OCR text gate.

        Pulls the director from credits, the production company names, and the
        public TMDB tagline. Missing values stay ``None`` / ``[]``.
        """
        data = await self._get(
            f"/movie/{tmdb_id}",
            params={"append_to_response": "credits"},
        )
        if not isinstance(data, dict):
            return MovieDetails()

        credits = data.get("credits")
        crew = credits.get("crew", []) if isinstance(credits, dict) else []

        director = None
        for member in crew:
            if not isinstance(member, dict) or member.get("job") != "Director":
                continue
            name = str(member.get("name") or "").strip()
            if name:
                director = name
                break

        companies: list[str] = []
        for company in data.get("production_companies", []):
            if not isinstance(company, dict):
                continue
            name = str(company.get("name") or "").strip()
            if name and name not in companies:
                companies.append(name)

        tagline = str(data.get("tagline") or "").strip() or None
        return MovieDetails(
            director=director,
            production_companies=companies,
            tagline=tagline,
        )

    # ── TV Endpoints ─────────────────────────────────────────────────

    async def get_tv_images(
        self,
        tmdb_id: int,
        include_language: str = "en,null",
    ) -> list[PosterCandidate]:
        """Fetch all poster candidates for a TV series.

        Same semantics as ``get_movie_images()`` but for TV shows.
        """
        data = await self._get(
            f"/tv/{tmdb_id}/images",
            params={"include_image_language": include_language},
        )
        return self._parse_posters(data)

    async def get_season_images(
        self,
        tmdb_id: int,
        season_number: int,
        include_language: str = "en,null",
    ) -> list[PosterCandidate]:
        """Fetch all poster candidates for a specific season of a TV series.

        Args:
            tmdb_id: TMDB numeric series ID.
            season_number: 1-based season number.
            include_language: Same as ``get_movie_images()``.

        Returns:
            List of PosterCandidate, sorted by vote_average descending.
        """
        data = await self._get(
            f"/tv/{tmdb_id}/season/{season_number}/images",
            params={"include_image_language": include_language},
        )
        return self._parse_posters(data)

    async def get_tv_primary_poster(self, tmdb_id: int) -> str | None:
        """Return the filename of the TV series' TMDB primary poster.

        Same semantics as ``get_movie_primary_poster()`` but for TV shows.
        """
        data = await self._get(f"/tv/{tmdb_id}")
        poster_path = data.get("poster_path") if isinstance(data, dict) else None
        if not poster_path:
            return None
        return poster_path.lstrip("/").split("/")[-1]

    async def get_season_primary_poster(self, tmdb_id: int, season_number: int) -> str | None:
        """Return the filename of the season's TMDB primary poster.

        Same semantics as ``get_movie_primary_poster()`` but for a TV season.
        """
        data = await self._get(f"/tv/{tmdb_id}/season/{season_number}")
        poster_path = data.get("poster_path") if isinstance(data, dict) else None
        if not poster_path:
            return None
        return poster_path.lstrip("/").split("/")[-1]

    async def get_tv_external_ids(self, tmdb_id: int) -> dict:
        """Fetch external IDs (tvdb_id, imdb_id) for a TV series.

        Returns a dict with keys like ``tvdb_id``, ``imdb_id``, ``freebase_mid``, etc.
        """
        return await self._get(f"/tv/{tmdb_id}/external_ids")

    async def get_tv_details(self, tmdb_id: int) -> TVDetails:
        """Fetch TV series metadata used by the OCR text gate.

        Pulls the first creator's name as director from created_by,
        production company and network names, and tagline.
        """
        data = await self._get(f"/tv/{tmdb_id}")
        if not isinstance(data, dict):
            return TVDetails()

        director = None
        created_by = data.get("created_by", [])
        if isinstance(created_by, list) and created_by:
            first_creator = created_by[0]
            if isinstance(first_creator, dict):
                director = str(first_creator.get("name") or "").strip() or None

        companies: list[str] = []
        for source_key in ("networks", "production_companies"):
            for company in data.get(source_key, []):
                if not isinstance(company, dict):
                    continue
                name = str(company.get("name") or "").strip()
                if name and name not in companies:
                    companies.append(name)

        tagline = str(data.get("tagline") or "").strip() or None
        return TVDetails(
            director=director,
            production_companies=companies,
            tagline=tagline,
        )

    # ── ID Resolution ────────────────────────────────────────────────

    async def find_by_external_id(
        self,
        external_id: str,
        source: str,
    ) -> dict | None:
        """Resolve a TMDB ID from an external identifier.

        Args:
            external_id: The external ID string (e.g. ``"tt1234567"`` for IMDB).
            source: The external source name — one of:
                ``"imdb_id"``, ``"tvdb_id"``, ``"freebase_mid"``,
                ``"freebase_id"``, ``"tvrage_id"``, ``"wikidata_id"``,
                ``"facebook_id"``, ``"instagram_id"``, ``"twitter_id"``.

        Returns:
            A dict containing the matched result, or ``None`` if no match.
            For TV shows, the dict has a ``tv_results`` key.
            For movies, the dict has a ``movie_results`` key.
            Each list entry has an ``id`` field (the TMDB ID).

        Example:
            >>> result = await client.find_by_external_id("tt0903747", "imdb_id")
            >>> result["tv_results"][0]["id"]   # → 1396 (Breaking Bad)
        """
        return await self._get(
            f"/find/{external_id}",
            params={"external_source": source},
        )
