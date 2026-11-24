"""Radarr paths to local paths. The translation and traversal cases came from the old
``path_utils`` tests; the Sonarr cases and the ceiling machinery did not."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from marquee.config import Settings
from marquee.fs.paths import (
    PathMapper,
    PathMappingError,
    RootMapping,
    UnmappedPathError,
    UnsafePathError,
)


def _mapper(*pairs: tuple[str, str]) -> PathMapper:
    return PathMapper([RootMapping(PurePosixPath(remote), Path(local)) for remote, local in pairs])


def _from_settings(**values: object) -> PathMapper:
    return PathMapper.from_settings(Settings(_env_file=None, **values))


# ── Translation ───────────────────────────────────────────────────────────


def test_translates_a_radarr_path() -> None:
    mapper = _mapper(("/plunder/movies", "/Volumes/PLUNDER/Media/Movies"))
    assert mapper.to_local("/plunder/movies/Dune (2021)") == Path(
        "/Volumes/PLUNDER/Media/Movies/Dune (2021)"
    )


def test_settings_pair_radarr_roots_with_movie_roots_by_position() -> None:
    mapper = _from_settings(
        MOVIE_ROOTS="/media/movies,/media/archive",
        RADARR_MOVIE_ROOTS="/data/movies,/data/archive",
    )
    assert mapper.to_local("/data/movies/4K/Dune (2021)") == Path("/media/movies/4K/Dune (2021)")
    assert mapper.to_local("/data/archive/Metropolis (1927)") == Path(
        "/media/archive/Metropolis (1927)"
    )


def test_without_radarr_roots_each_movie_root_maps_to_itself() -> None:
    mapper = _from_settings(MOVIE_ROOTS="/plunder/movies")
    assert mapper.to_local("/plunder/movies/Dune (2021)") == Path("/plunder/movies/Dune (2021)")


def test_the_container_side_follows_movie_roots() -> None:
    # Compose overrides MOVIE_ROOTS inside the container; the Radarr side is unchanged.
    host = _from_settings(MOVIE_ROOTS="/mnt/library/Movies", RADARR_MOVIE_ROOTS="/data/movies")
    container = _from_settings(MOVIE_ROOTS="/media/movies", RADARR_MOVIE_ROOTS="/data/movies")
    remote = "/data/movies/4K/Alien - Romulus (2024)"
    assert host.to_local(remote) == Path("/mnt/library/Movies/4K/Alien - Romulus (2024)")
    assert container.to_local(remote) == Path("/media/movies/4K/Alien - Romulus (2024)")


def test_a_path_no_root_covers_is_refused() -> None:
    mapper = _mapper(("/plunder/movies", "/Volumes/PLUNDER/Media/Movies"))
    with pytest.raises(UnmappedPathError, match="no movie root covers"):
        mapper.to_local("/some/other/path/Dune")
    assert not mapper.covers("/some/other/path/Dune")


def test_translation_requires_a_complete_prefix_component() -> None:
    mapper = _mapper(("/plunder/movies", "/movies"))
    with pytest.raises(UnmappedPathError):
        mapper.to_local("/plunder/movies-private/secret")
    with pytest.raises(UnmappedPathError):
        _mapper(("/movies", "/media/movies")).to_local("/movies2/Dune")


def test_translation_handles_trailing_slashes_and_the_root_itself() -> None:
    mapper = _mapper(("/plunder/movies/", "/movies/"))
    assert mapper.to_local("/plunder/movies/Dune") == Path("/movies/Dune")
    assert mapper.to_local("/plunder/movies/") == Path("/movies")
    assert mapper.to_local("/plunder//movies/./Dune/") == Path("/movies/Dune")


def test_a_filesystem_root_prefix_on_the_radarr_side_works() -> None:
    assert _mapper(("/", "/movies")).to_local("/Dune") == Path("/movies/Dune")


def test_translation_uses_the_most_specific_root() -> None:
    mapper = _mapper(("/library", "/media/library"), ("/library/classics", "/media/classics"))
    assert mapper.to_local("/library/classics/Metropolis") == Path("/media/classics/Metropolis")
    assert mapper.to_local("/library/new/Arrival") == Path("/media/library/new/Arrival")


# ── Traversal and unsafe input ────────────────────────────────────────────


def test_rejects_a_null_byte() -> None:
    with pytest.raises(UnsafePathError, match="null byte"):
        _mapper(("/movies", "/media/movies")).to_local("/movies/\0etc")


@pytest.mark.parametrize(
    "remote",
    [
        "/movies/../etc/passwd",
        "/movies/Dune/../../etc",
        "/movies/..",
    ],
)
def test_rejects_dot_dot_instead_of_collapsing_it(remote: str) -> None:
    with pytest.raises(UnsafePathError, match=r"'\.\.'"):
        _mapper(("/movies", "/media/movies")).to_local(remote)


@pytest.mark.parametrize("remote", ["", "movies/Dune", "Dune (2021)"])
def test_rejects_empty_and_relative_paths(remote: str) -> None:
    with pytest.raises(UnmappedPathError):
        _mapper(("/movies", "/media/movies")).to_local(remote)


def test_no_roots_fails_closed() -> None:
    with pytest.raises(PathMappingError, match="no movie roots"):
        _from_settings()


def test_a_local_root_cannot_be_the_whole_filesystem() -> None:
    with pytest.raises(PathMappingError, match="whole filesystem"):
        _mapper(("/movies", "/"))


def test_a_local_root_must_be_absolute() -> None:
    with pytest.raises(PathMappingError, match="not an absolute path"):
        _mapper(("/movies", "media/movies"))


def test_radarr_roots_must_be_distinct() -> None:
    with pytest.raises(PathMappingError, match="twice"):
        _mapper(("/movies", "/media/a"), ("/movies/", "/media/b"))


def test_translation_touches_no_file(tmp_path: Path) -> None:
    mapper = _mapper(("/movies", str(tmp_path / "absent")))
    assert mapper.to_local("/movies/Dune") == tmp_path / "absent" / "Dune"
    assert not (tmp_path / "absent").exists()
