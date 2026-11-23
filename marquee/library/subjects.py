"""Resolve what the user typed to one movie and its TMDB identity.

Without a TMDB id, the title is matched against the scanned movie folders, ignoring case
and punctuation, on the whole title: "Die Hard" finds ``Die Hard (1988)`` and not
``A Good Day to Die Hard (2013)``. The folder's movie file names carry the IMDb id, and
TMDB's find-by-external-id call turns that into the TMDB id and TMDB's own title, which is
what the OCR gate matches against (folder names lose characters such as ``:``).

M3's library tables replace this lookup.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from marquee.library.folders import MovieFolder, split_title_year

CLOSEST_MATCHES = 5


class SubjectLookupError(Exception):
    """The typed title does not lead to exactly one movie; the message says why."""


class ExternalIdFinder(Protocol):
    async def find_by_external_id(self, external_id: str, source: str) -> dict | None: ...


@dataclass(frozen=True)
class ResolvedSubject:
    title: str
    """TMDB's title, or the title as typed when a TMDB id was given."""
    tmdb_id: int
    imdb_id: str | None = None
    folder: MovieFolder | None = None


def normalise_title(text: str) -> str:
    """Lower case, accents and apostrophes dropped, other punctuation as spaces."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = re.sub(r"['’`]", "", stripped.casefold())
    return re.sub(r"[^0-9a-z]+", " ", lowered).strip()


def match_folders(
    query: str, folders: Sequence[MovieFolder], *, year: int | None = None
) -> list[MovieFolder]:
    """Folders whose whole title equals the query once normalised (and the year, if given)."""
    title, typed_year = split_title_year(query)
    year = year if year is not None else typed_year
    wanted = normalise_title(title)
    return [
        folder
        for folder in folders
        if normalise_title(folder.title) == wanted and (year is None or folder.year == year)
    ]


def closest_folder_names(query: str, folders: Sequence[MovieFolder]) -> list[str]:
    by_title: dict[str, list[str]] = {}
    for folder in folders:
        by_title.setdefault(normalise_title(folder.title), []).append(folder.name)
    wanted = normalise_title(split_title_year(query)[0])
    close = difflib.get_close_matches(wanted, list(by_title), n=CLOSEST_MATCHES, cutoff=0.5)
    if not close:
        close = [title for title in by_title if wanted and wanted in title][:CLOSEST_MATCHES]
    return [name for title in close for name in by_title[title]][:CLOSEST_MATCHES]


def pick_folder(
    query: str, folders: Sequence[MovieFolder], *, year: int | None = None
) -> MovieFolder:
    """The one folder the query names, or a :class:`SubjectLookupError` saying why not."""
    matches = match_folders(query, folders, year=year)
    if not matches:
        closest = closest_folder_names(query, folders)
        hint = f"; closest: {', '.join(closest)}" if closest else ""
        year_note = f" from {year}" if year is not None else ""
        raise SubjectLookupError(f"no movie folder titled {query!r}{year_note}{hint}")
    imdb_ids = {folder.imdb_id for folder in matches}
    if len(imdb_ids) > 1:
        names = ", ".join(f"{folder.quality}/{folder.name}" for folder in matches)
        raise SubjectLookupError(f"{query!r} matches several movies ({names}); add --year")
    folder = matches[0]
    if folder.imdb_id is None:
        raise SubjectLookupError(
            f"{folder.quality}/{folder.name} has no {{imdbid-tt…}} in its movie file name; "
            "pass --tmdb-id"
        )
    return folder


async def resolve_subject(
    query: str,
    *,
    client: ExternalIdFinder,
    folders: Sequence[MovieFolder] = (),
    year: int | None = None,
    tmdb_id: int | None = None,
) -> ResolvedSubject:
    """Turn what the user typed into a TMDB movie.

    With ``tmdb_id`` the title is used as typed and the library is not consulted.
    """
    if tmdb_id is not None:
        return ResolvedSubject(title=query, tmdb_id=tmdb_id)
    folder = pick_folder(query, folders, year=year)
    assert folder.imdb_id is not None
    found = await client.find_by_external_id(folder.imdb_id, "imdb_id")
    results = (found or {}).get("movie_results") or []
    if not results:
        raise SubjectLookupError(
            f"TMDB has no movie for {folder.imdb_id} ({folder.quality}/{folder.name})"
        )
    first = results[0]
    return ResolvedSubject(
        title=str(first.get("title") or folder.title),
        tmdb_id=int(first["id"]),
        imdb_id=folder.imdb_id,
        folder=folder,
    )
