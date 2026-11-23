"""Retrofit genres/years/tmdb_ids into an existing taste profile (design 11).

Adds the ``genres``, ``years``, and ``tmdb_ids`` keys to the profile ``.npz``
without re-embedding. Resolution order per poster (``"Title (Year).jpg"``):

  1. memoized cache (``training_data/.genre_cache.json``)
  2. the Marquee DB (``movies`` — synced from Radarr)
  3. TMDB ``/search/movie`` (one-time, only if a token is configured)

Usage:  python -m marquee.ml.profile_enrich [--no-tmdb]
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path

import numpy as np

from marquee.core.pipeline_config import pipeline_settings
from marquee.core.runtime_settings import effective_app_settings
from marquee.core.runtime_settings import effective_settings as settings
from marquee.ml.artifact_codec import (
    decode_unicode_list,
    ensure_safe_artifact,
    json_string_array,
    load_npz_safe,
    save_npz_atomic,
)

logger = logging.getLogger(__name__)

_YEAR = re.compile(r"\((\d{4})\)")
_YEAR_SUFFIX = re.compile(r"\s*\(\d{4}\)\s*$")


def _parse_name(name: str) -> tuple[str, int | None]:
    stem = Path(name).stem
    year_match = _YEAR.search(stem)
    year = int(year_match.group(1)) if year_match else None
    title = _YEAR_SUFFIX.sub("", stem).strip()
    title = re.sub(r"\s*-\s*\d+$", "", title).strip()  # drop " - 2" dedup suffix
    return title, year


def _db_index() -> dict[str, tuple[list[str], int | None, int | None]]:
    """{lower title: (genres, year, tmdb_id)} from the Marquee DB."""
    import psycopg  # noqa: PLC0415

    index: dict[str, tuple[list[str], int | None, int | None]] = {}
    db_url = settings.db_url_resolved.replace("postgresql+asyncpg://", "postgresql://", 1)
    try:
        with psycopg.connect(db_url) as conn, conn.cursor() as cursor:
            cursor.execute("SELECT title, year, genres, tmdb_id FROM movies")
            for title, year, genres_value, tmdb_id in cursor:
                try:
                    genres = (
                        json.loads(genres_value)
                        if isinstance(genres_value, str)
                        else (genres_value or [])
                    )
                except (json.JSONDecodeError, TypeError):
                    genres = []
                index[str(title).lower()] = (genres, year, tmdb_id)
    except Exception:
        logger.warning("Could not read movie metadata from PostgreSQL", exc_info=True)
    return index


def _tmdb_lookup(title: str, year: int | None) -> tuple[list[str], int | None] | None:
    token = effective_app_settings().TMDB_READ_ACCESS_TOKEN
    if not token:
        return None
    import httpx  # noqa: PLC0415

    # TMDB genre ids → names (movie list, stable).
    genre_map = {
        28: "Action",
        12: "Adventure",
        16: "Animation",
        35: "Comedy",
        80: "Crime",
        99: "Documentary",
        18: "Drama",
        10751: "Family",
        14: "Fantasy",
        36: "History",
        27: "Horror",
        10402: "Music",
        9648: "Mystery",
        10749: "Romance",
        878: "Science Fiction",
        10770: "TV Movie",
        53: "Thriller",
        10752: "War",
        37: "Western",
    }
    try:
        params = {"query": title}
        if year:
            params["year"] = str(year)
        resp = httpx.get(
            "https://api.themoviedb.org/3/search/movie",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15.0,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            return None
        top = results[0]
        genres = [genre_map[g] for g in top.get("genre_ids", []) if g in genre_map]
        return genres, top.get("id")
    except Exception as exc:  # noqa: BLE001
        logger.warning("TMDB lookup failed for %s: %s", title, exc)
        return None


def enrich(
    *,
    use_tmdb: bool = True,
    profile_path: Path | None = None,
    output: Path | None = None,
    cache_path: Path | None = None,
    progress_callback=None,
) -> Path:
    profile_path = profile_path or Path(pipeline_settings.TASTE_PROFILE_PATH)
    output = output or profile_path
    ensure_safe_artifact(profile_path, "taste_profile")
    with load_npz_safe(profile_path) as data:
        payload = {key: data[key] for key in data.files}
    names = decode_unicode_list(payload["poster_names"])

    cache_path = cache_path or output.with_name("genre-cache.json")
    cache = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
        except json.JSONDecodeError:
            cache = {}

    db_index = _db_index()
    genres_out: list[list[str]] = []
    years_out: list[int | None] = []
    tmdb_out: list[int | None] = []
    tmdb_calls = 0

    for index, name in enumerate(names):
        if progress_callback is not None:
            progress_callback(
                {
                    "stage": "resolve",
                    "processed": index,
                    "total": len(names),
                    "current_item": name,
                }
            )
        title, year = _parse_name(name)
        key = f"{title.lower()}|{year}"
        if key in cache:
            entry = cache[key]
        elif title.lower() in db_index:
            genres, db_year, tmdb_id = db_index[title.lower()]
            entry = {"genres": genres, "year": year or db_year, "tmdb_id": tmdb_id}
        elif use_tmdb and (result := _tmdb_lookup(title, year)) is not None:
            tmdb_calls += 1
            genres, tmdb_id = result
            entry = {"genres": genres, "year": year, "tmdb_id": tmdb_id}
        else:
            entry = {"genres": [], "year": year, "tmdb_id": None}
        cache[key] = entry
        genres_out.append(entry["genres"])
        years_out.append(entry["year"] if entry["year"] is not None else 0)
        tmdb_out.append(entry["tmdb_id"] if entry["tmdb_id"] is not None else 0)

    if progress_callback is not None and names:
        progress_callback({"stage": "resolve", "processed": len(names), "total": len(names)})
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")

    payload["genres_json"] = json_string_array(genres_out)
    payload["years"] = np.asarray(years_out, dtype=np.int64)
    payload["tmdb_ids"] = np.asarray(tmdb_out, dtype=np.int64)
    payload.pop("genres", None)
    save_npz_atomic(output, payload)

    resolved = sum(1 for g in genres_out if g)
    logger.info(
        "ENRICH | %d posters: %d with genres (%d TMDB calls)",
        len(names),
        resolved,
        tmdb_calls,
    )
    return output


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-tmdb", action="store_true", help="DB + cache only")
    args = parser.parse_args()
    path = enrich(use_tmdb=not args.no_tmdb)
    print(f"[INFO] Enriched profile written to {path}")


if __name__ == "__main__":
    main()
