from __future__ import annotations

from pathlib import Path

import pytest

from marquee.library.folders import MovieFolder, split_title_year
from marquee.library.subjects import (
    SubjectLookupError,
    match_folders,
    normalise_title,
    resolve_subject,
)


def _folder(name: str, imdb_id: str | None, quality: str = "1080p") -> MovieFolder:
    title, year = split_title_year(name)
    return MovieFolder(
        path=Path("/media") / quality / name,
        quality=quality,
        name=name,
        title=title,
        year=year,
        imdb_id=imdb_id,
        poster=None,
        videos=(),
        images=(),
        file_named_images=(),
    )


FOLDERS = [
    _folder("Die Hard (1988)", "tt0095016"),
    _folder("A Good Day to Die Hard (2013)", "tt1606378", "4K"),
    _folder("Mission - Impossible (1996)", "tt0117060", "4K"),
    _folder("Ocean's Eleven (2001)", "tt0240772", "4K"),
    _folder("2010 (1984)", "tt0086837"),
    _folder("Dune (1984)", "tt0087182"),
    _folder("Dune (2021)", "tt1160419", "4K"),
    _folder("No Id (2000)", None),
]


class FakeTMDB:
    def __init__(self, results: dict[str, list[dict]]) -> None:
        self.results = results
        self.calls: list[tuple[str, str]] = []

    async def find_by_external_id(self, external_id: str, source: str) -> dict | None:
        self.calls.append((external_id, source))
        return {"movie_results": self.results.get(external_id, []), "tv_results": []}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Mission: Impossible", "mission impossible"),
        ("Mission - Impossible", "mission impossible"),
        ("Ocean's Eleven", "oceans eleven"),
        ("  DIE   hard ", "die hard"),
        ("Amélie", "amelie"),
    ],
)
def test_normalise_title(text: str, expected: str) -> None:
    assert normalise_title(text) == expected


def test_whole_title_match_only() -> None:
    assert [f.name for f in match_folders("Die Hard", FOLDERS)] == ["Die Hard (1988)"]
    assert [f.name for f in match_folders("die hard", FOLDERS)] == ["Die Hard (1988)"]
    assert match_folders("Hard", FOLDERS) == []


def test_match_ignores_punctuation() -> None:
    assert [f.name for f in match_folders("Mission: Impossible", FOLDERS)] == [
        "Mission - Impossible (1996)"
    ]
    assert [f.name for f in match_folders("Oceans Eleven", FOLDERS)] == ["Ocean's Eleven (2001)"]


def test_year_narrows_and_can_be_typed() -> None:
    assert [f.name for f in match_folders("Dune", FOLDERS, year=2021)] == ["Dune (2021)"]
    assert [f.name for f in match_folders("Dune (1984)", FOLDERS)] == ["Dune (1984)"]


async def test_resolves_through_the_imdb_id_and_takes_tmdb_title() -> None:
    tmdb = FakeTMDB({"tt0117060": [{"id": 954, "title": "Mission: Impossible"}]})

    subject = await resolve_subject("Mission Impossible", client=tmdb, folders=FOLDERS)

    assert tmdb.calls == [("tt0117060", "imdb_id")]
    assert subject.tmdb_id == 954
    assert subject.title == "Mission: Impossible"
    assert subject.imdb_id == "tt0117060"
    assert subject.folder is not None and subject.folder.name == "Mission - Impossible (1996)"


async def test_numeric_title() -> None:
    tmdb = FakeTMDB({"tt0086837": [{"id": 4437, "title": "2010"}]})

    subject = await resolve_subject("2010", client=tmdb, folders=FOLDERS)

    assert (subject.tmdb_id, subject.title) == (4437, "2010")


async def test_tmdb_id_skips_the_library() -> None:
    tmdb = FakeTMDB({})

    subject = await resolve_subject("Anything At All", client=tmdb, folders=(), tmdb_id=562)

    assert (subject.title, subject.tmdb_id, subject.folder) == ("Anything At All", 562, None)
    assert tmdb.calls == []


async def test_no_match_names_the_closest_folders() -> None:
    with pytest.raises(SubjectLookupError) as exc:
        await resolve_subject("Die Hrd", client=FakeTMDB({}), folders=FOLDERS)

    assert "no movie folder titled 'Die Hrd'" in str(exc.value)
    assert "Die Hard (1988)" in str(exc.value)


async def test_several_movies_ask_for_a_year() -> None:
    with pytest.raises(SubjectLookupError, match="several movies.*add --year"):
        await resolve_subject("Dune", client=FakeTMDB({}), folders=FOLDERS)


async def test_a_folder_without_an_imdb_id_asks_for_a_tmdb_id() -> None:
    with pytest.raises(SubjectLookupError, match="pass --tmdb-id"):
        await resolve_subject("No Id", client=FakeTMDB({}), folders=FOLDERS)


async def test_tmdb_without_a_movie_result_fails() -> None:
    with pytest.raises(SubjectLookupError, match="TMDB has no movie for tt0095016"):
        await resolve_subject("Die Hard", client=FakeTMDB({}), folders=FOLDERS)
