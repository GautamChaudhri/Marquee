"""The only place a path from Radarr becomes a local path.

Radarr reports movie folders as it sees them (``/data/movies/4K/Dune (2021)``). Marquee sees
the same folders somewhere else, and the host and the container see them at different
places again. The database stores Radarr's paths only; this module works out the local path
each time one is needed.

Each folder in ``MOVIE_ROOTS`` pairs, by position, with the folder ``RADARR_MOVIE_ROOTS``
names for it. Without ``RADARR_MOVIE_ROOTS``, each movie root maps to itself: Radarr sees the
library where Marquee does.

Translation is purely lexical: nothing here touches the filesystem. It matches whole path
components, so ``/movies2`` is not under ``/movies``, and it refuses null bytes and ``..``
components outright rather than collapse them. Code that opens a translated path goes
through :mod:`marquee.fs.boundary`, which also refuses symbolic links.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from marquee.config import Settings, settings


class PathMappingError(ValueError):
    """A path from Radarr cannot be turned into a local path, or the mapping is unusable."""


class UnmappedPathError(PathMappingError):
    """No movie root covers the path."""


class UnsafePathError(PathMappingError):
    """The path holds a null byte or a ``..`` component, or would leave its local root."""


@dataclass(frozen=True)
class RootMapping:
    """One movie root: where Radarr sees it, and where it is here."""

    remote: PurePosixPath
    local: Path


def _parse_remote(remote_path: str | PurePosixPath) -> PurePosixPath:
    raw = str(remote_path)
    if "\0" in raw:
        raise UnsafePathError("path from Radarr contains a null byte")
    if not raw:
        raise UnmappedPathError("path from Radarr is empty")
    path = PurePosixPath(raw)
    if ".." in path.parts:
        raise UnsafePathError(f"path from Radarr has a '..' component: {raw!r}")
    if not path.is_absolute():
        raise UnmappedPathError(f"path from Radarr is not absolute: {raw!r}")
    return path


class PathMapper:
    """Turns Radarr's paths into local ones, for one set of root pairs."""

    def __init__(self, mappings: Sequence[RootMapping]) -> None:
        if not mappings:
            raise PathMappingError("no movie roots configured (set MOVIE_ROOTS)")
        seen: set[PurePosixPath] = set()
        for mapping in mappings:
            local = Path(mapping.local)
            if not local.is_absolute():
                raise PathMappingError(f"movie root {str(local)!r} is not an absolute path")
            if local == Path(local.anchor):
                raise PathMappingError("a movie root cannot be the whole filesystem")
            remote = _parse_remote(mapping.remote)
            if remote in seen:
                raise PathMappingError(f"RADARR_MOVIE_ROOTS names {str(remote)!r} twice")
            seen.add(remote)
        # Most specific first, so a root nested inside another wins.
        self._mappings = tuple(
            sorted(
                (RootMapping(_parse_remote(m.remote), Path(m.local)) for m in mappings),
                key=lambda m: len(m.remote.parts),
                reverse=True,
            )
        )

    @classmethod
    def from_settings(cls, config: Settings | None = None) -> PathMapper:
        config = config or settings
        locals_ = list(config.MOVIE_ROOTS)
        remotes = list(config.RADARR_MOVIE_ROOTS) or [PurePosixPath(p) for p in locals_]
        if len(remotes) != len(locals_):
            raise PathMappingError(
                f"RADARR_MOVIE_ROOTS has {len(remotes)} entries and MOVIE_ROOTS has "
                f"{len(locals_)}; they pair by position"
            )
        return cls(
            [RootMapping(remote, local) for remote, local in zip(remotes, locals_, strict=True)]
        )

    @property
    def mappings(self) -> tuple[RootMapping, ...]:
        return self._mappings

    def covers(self, remote_path: str | PurePosixPath) -> bool:
        try:
            self.to_local(remote_path)
        except PathMappingError:
            return False
        return True

    def to_local(self, remote_path: str | PurePosixPath) -> Path:
        """The local path for a path Radarr reported. Touches no file."""
        remote = _parse_remote(remote_path)
        for mapping in self._mappings:
            root_parts = mapping.remote.parts
            if remote.parts[: len(root_parts)] != root_parts:
                continue
            local = mapping.local.joinpath(*remote.parts[len(root_parts) :])
            if not local.is_relative_to(mapping.local):
                raise UnsafePathError(f"path from Radarr leaves its movie root: {str(remote)!r}")
            return local
        raise UnmappedPathError(
            f"no movie root covers {str(remote)!r}; check RADARR_MOVIE_ROOTS and MOVIE_ROOTS"
        )


def to_local(remote_path: str | PurePosixPath) -> Path:
    """The local path for a path Radarr reported, under the configured roots."""
    return PathMapper.from_settings().to_local(remote_path)
