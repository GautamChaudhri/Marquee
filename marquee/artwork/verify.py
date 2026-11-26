"""``marquee deployments verify``: is every deployed poster, and every backup, what the
records say? Read-only: nothing here writes to the library or the data folder.

Two checks:

- **Posters.** For each movie's newest deployment, the movie's ``poster.jpg`` is read and
  hashed: it is the file the deployment recorded, it was replaced since (by the owner, by
  hand or with another tool), or it is gone. A restore of "no poster" expects no file. A
  folder that cannot be read (the share is not mounted) is reported as unreadable.
- **Backups.** Every backup a deployment names (the poster that deployment replaced) must
  exist and hold the bytes its recorded hash says. A missing or damaged backup is the one
  finding that fails the command: it is the way back, and nothing else holds it.

Times are never compared, only hashes: the share shows files dated in the future.
"""

from __future__ import annotations

import asyncio
import os
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.artwork import backup
from marquee.config import settings
from marquee.db.models import Deployment, Movie
from marquee.fs.boundary import FilesystemBoundaryError, boundary_for_roots
from marquee.fs.paths import PathMapper, PathMappingError
from marquee.library.folders import POSTER_NAME
from marquee.library.sync import hash_poster

PosterState = Literal["as_recorded", "replaced", "gone", "unreadable"]
BackupState = Literal["ok", "missing", "damaged"]


@dataclass(frozen=True)
class PosterCheck:
    movie_id: int
    title: str
    deployment_id: int
    action: str
    initiated_by: str
    state: PosterState
    recorded_sha256: str | None
    """What the deployment left in the folder; ``None`` when it removed the poster."""
    found_sha256: str | None
    note: str | None = None


@dataclass(frozen=True)
class BackupCheck:
    backup_file: str
    movie_id: int
    sha256: str | None
    deployment_ids: list[int]
    state: BackupState
    note: str | None = None


@dataclass
class VerifyReport:
    posters: list[PosterCheck] = field(default_factory=list)
    backups: list[BackupCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """False when a backup is missing or damaged; a changed poster is the owner's act."""
        return all(check.state == "ok" for check in self.backups)

    def counts(self) -> dict[str, dict[str, int]]:
        posters = dict.fromkeys(("as_recorded", "replaced", "gone", "unreadable"), 0)
        backups = dict.fromkeys(("ok", "missing", "damaged"), 0)
        for poster in self.posters:
            posters[poster.state] += 1
        for check in self.backups:
            backups[check.state] += 1
        return {"posters": posters, "backups": backups}

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "counts": self.counts(),
            "posters": [asdict(check) for check in self.posters],
            "backups": [asdict(check) for check in self.backups],
        }


def _label(movie: Movie) -> str:
    return f"{movie.title} ({movie.year})" if movie.year else movie.title


def check_poster(movie: Movie, deployment: Deployment, mapper: PathMapper) -> PosterCheck:
    """Read the movie's poster and compare it with its newest deployment. Blocking."""
    base: dict[str, Any] = {
        "movie_id": movie.id,
        "title": _label(movie),
        "deployment_id": deployment.id,
        "action": deployment.action,
        "initiated_by": deployment.initiated_by,
        "recorded_sha256": deployment.new_sha256,
    }
    try:
        folder = mapper.to_local(movie.radarr_path)
    except PathMappingError as exc:
        return PosterCheck(**base, state="unreadable", found_sha256=None, note=str(exc))
    try:
        if not folder.is_dir() or folder.is_symlink():
            return PosterCheck(
                **base,
                state="unreadable",
                found_sha256=None,
                note="the movie's folder is missing or not a plain folder (is the share mounted?)",
            )
        poster = folder / POSTER_NAME
        try:
            mode = os.lstat(poster).st_mode
        except FileNotFoundError:
            found = None
        else:
            if not stat.S_ISREG(mode):
                return PosterCheck(
                    **base,
                    state="replaced",
                    found_sha256=None,
                    note=f"{POSTER_NAME} is not a regular file",
                )
            boundary = boundary_for_roots(
                {f"movies{i}": m.local for i, m in enumerate(mapper.mappings)},
                access="read",
                purpose="movie library",
            )
            found, _ = hash_poster(boundary, poster)
    except (OSError, FilesystemBoundaryError) as exc:
        return PosterCheck(**base, state="unreadable", found_sha256=None, note=str(exc))
    if found == deployment.new_sha256:
        return PosterCheck(**base, state="as_recorded", found_sha256=found)
    if found is None:
        return PosterCheck(**base, state="gone", found_sha256=None)
    return PosterCheck(
        **base,
        state="replaced",
        found_sha256=found,
        note=(
            "a poster appeared where the restore left none"
            if deployment.new_sha256 is None
            else "the file is not the one this deployment wrote"
        ),
    )


def check_backup(key: str, sha256: str | None, backups_dir: Path) -> tuple[BackupState, str | None]:
    """Whether the backup ``key`` exists and holds ``sha256``. Blocking; creates nothing."""
    if sha256 is None:
        return "damaged", "the deployment names a backup but no hash for it"
    try:
        data = backup.read_only(key, backups_dir)
    except backup.BackupError as exc:
        return "damaged", str(exc)
    if data is None:
        return "missing", None
    actual = backup.sha256_of(data)
    if actual != sha256:
        return "damaged", f"holds bytes with hash {actual}"
    return "ok", None


async def verify_deployments(
    session: AsyncSession,
    *,
    mapper: PathMapper | None = None,
    backups_dir: Path | None = None,
) -> VerifyReport:
    """Check every movie's newest deployment and every backup a deployment names."""
    deployments = list(
        await session.scalars(select(Deployment).order_by(Deployment.movie_id, Deployment.id))
    )
    movie_ids = {row.movie_id for row in deployments}
    movies = {
        movie.id: movie
        for movie in await session.scalars(select(Movie).where(Movie.id.in_(movie_ids)))
    }
    await session.commit()
    mapper = mapper or PathMapper.from_settings(settings)
    base = backups_dir or settings.backups_dir
    report = VerifyReport()

    newest: dict[int, Deployment] = {}
    for row in deployments:
        newest[row.movie_id] = row
    for movie_id, row in sorted(newest.items()):
        report.posters.append(await asyncio.to_thread(check_poster, movies[movie_id], row, mapper))

    named: dict[str, list[Deployment]] = {}
    for row in deployments:
        if row.backup_file is not None:
            named.setdefault(row.backup_file, []).append(row)
        elif row.previous_sha256 is not None:
            report.backups.append(
                BackupCheck(
                    backup_file="",
                    movie_id=row.movie_id,
                    sha256=row.previous_sha256,
                    deployment_ids=[row.id],
                    state="missing",
                    note="the deployment replaced a poster but recorded no backup",
                )
            )
    for key, rows in sorted(named.items()):
        hashes = {row.previous_sha256 for row in rows}
        sha256 = rows[0].previous_sha256 if len(hashes) == 1 else None
        state, note = await asyncio.to_thread(check_backup, key, sha256, base)
        if len(hashes) > 1:
            note = "deployments name this backup with different hashes"
        report.backups.append(
            BackupCheck(
                backup_file=key,
                movie_id=rows[0].movie_id,
                sha256=sha256,
                deployment_ids=[row.id for row in rows],
                state=state,
                note=note,
            )
        )
    return report
