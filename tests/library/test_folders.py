from __future__ import annotations

from pathlib import Path

import pytest

from marquee.library.folders import (
    LibraryRootError,
    imdb_id_from_name,
    scan_movie_roots,
    scan_root,
    split_title_year,
)


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def _movie(root: Path, quality: str, name: str, imdb: str | None, *extra: str) -> Path:
    folder = root / quality / name
    folder.mkdir(parents=True)
    tag = f" {{imdbid-{imdb}}}" if imdb else ""
    _touch(folder / f"{name}{tag} - [Bluray-1080p].mkv")
    for file_name in extra:
        _touch(folder / file_name)
    return folder


@pytest.fixture
def library(tmp_path: Path) -> Path:
    root = tmp_path / "Movies"
    _movie(root, "1080p", "Die Hard (1988)", "tt0095016", "poster.jpg")
    _movie(root, "1080p", "No Poster (2001)", "tt0000001", "fanart.jpg")
    _movie(
        root,
        "4K",
        "File Named (2024)",
        "tt0000002",
        "File Named (2024) {imdbid-tt0000002} - [Bluray-1080p].jpg",
    )
    _movie(root, "4K", "Untitled Year", None, "poster.jpg")
    # Hidden entries at every level are not titles and are never read.
    _touch(root / ".marquee" / "state.json")
    _movie(root, "4K", ".hidden (1999)", "tt0000003", "poster.jpg")
    _touch(root / "1080p" / "Die Hard (1988)" / ".hidden.jpg")
    # A sub-folder below a title folder is ignored.
    _touch(root / "1080p" / "Die Hard (1988)" / "Featurettes" / "poster.jpg")
    return root


def test_scan_lists_visible_title_folders_in_order(library: Path) -> None:
    folders = scan_root(library)

    assert [(f.quality, f.name) for f in folders] == [
        ("1080p", "Die Hard (1988)"),
        ("1080p", "No Poster (2001)"),
        ("4K", "File Named (2024)"),
        ("4K", "Untitled Year"),
    ]


def test_scan_reads_title_year_imdb_and_poster(library: Path) -> None:
    die_hard = scan_root(library)[0]

    assert die_hard.title == "Die Hard"
    assert die_hard.year == 1988
    assert die_hard.imdb_id == "tt0095016"
    assert die_hard.poster == library / "1080p" / "Die Hard (1988)" / "poster.jpg"
    assert [v.name for v in die_hard.videos] == [
        "Die Hard (1988) {imdbid-tt0095016} - [Bluray-1080p].mkv"
    ]
    assert die_hard.images == ()
    assert not die_hard.has_file_named_image


def test_folder_with_no_poster_keeps_its_other_images(library: Path) -> None:
    no_poster = scan_root(library)[1]

    assert no_poster.poster is None
    assert [i.name for i in no_poster.images] == ["fanart.jpg"]
    assert not no_poster.has_file_named_image


def test_image_named_after_the_movie_file_is_marked(library: Path) -> None:
    file_named = scan_root(library)[2]

    assert file_named.poster is None
    assert file_named.has_file_named_image
    assert file_named.file_named_images == file_named.images
    assert file_named.file_named_images[0].stem == file_named.videos[0].stem


def test_folder_without_a_year(library: Path) -> None:
    untitled = scan_root(library)[3]

    assert untitled.title == "Untitled Year"
    assert untitled.year is None
    assert untitled.imdb_id is None
    assert untitled.poster is not None


def test_quality_folders_are_not_hard_coded(tmp_path: Path) -> None:
    _movie(tmp_path, "720p", "Alien (1979)", "tt0078748", "poster.jpg")

    (folder,) = scan_root(tmp_path)

    assert folder.quality == "720p"


def test_conflicting_imdb_ids_give_none(tmp_path: Path) -> None:
    folder = _movie(tmp_path, "1080p", "Twice (2000)", "tt0000010")
    _touch(folder / "Twice (2000) {imdbid-tt0000011} - [WEBDL-1080p].mp4")

    (twice,) = scan_root(tmp_path)

    assert twice.imdb_id is None
    assert len(twice.videos) == 2


def test_missing_root_raises(tmp_path: Path) -> None:
    with pytest.raises(LibraryRootError, match="does not exist"):
        scan_root(tmp_path / "absent")


def test_root_that_is_a_file_raises(tmp_path: Path) -> None:
    with pytest.raises(LibraryRootError, match="not a directory"):
        scan_root(_touch(tmp_path / "file"))


def test_root_with_only_hidden_folders_raises(tmp_path: Path) -> None:
    (tmp_path / ".marquee").mkdir()

    with pytest.raises(LibraryRootError, match="no visible sub-folder"):
        scan_root(tmp_path)


def test_no_roots_configured_raises() -> None:
    with pytest.raises(LibraryRootError, match="MOVIE_ROOTS"):
        scan_movie_roots([])


def test_scan_movie_roots_keeps_root_order(tmp_path: Path) -> None:
    _movie(tmp_path / "b", "1080p", "Zulu (1964)", "tt0058777")
    _movie(tmp_path / "a", "1080p", "Alien (1979)", "tt0078748")

    names = [f.name for f in scan_movie_roots([tmp_path / "b", tmp_path / "a"])]

    assert names == ["Zulu (1964)", "Alien (1979)"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Die Hard (1988)", ("Die Hard", 1988)),
        ("2010 (1984)", ("2010", 1984)),
        ("Untitled", ("Untitled", None)),
        ("(1999)", ("(1999)", None)),
        ("Blade Runner 2049 (2017)", ("Blade Runner 2049", 2017)),
    ],
)
def test_split_title_year(name: str, expected: tuple[str, int | None]) -> None:
    assert split_title_year(name) == expected


def test_imdb_id_from_name() -> None:
    name = "Die Hard (1988) {imdbid-tt0095016} - Theatrical Cut [Bluray-1080p].mkv"
    assert imdb_id_from_name(name) == "tt0095016"
    assert imdb_id_from_name("Die Hard (1988).mkv") is None
