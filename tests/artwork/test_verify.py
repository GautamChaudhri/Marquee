"""``marquee deployments verify``: deployed posters and their backups against the records,
on scratch folders under tmp_path. Nothing it does may write."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import pytest
from pydantic import SecretStr
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from marquee import cli
from marquee.artwork import verify
from marquee.config import settings
from marquee.db.engine import create_session_factory
from marquee.db.models import Deployment, Movie
from marquee.fs.paths import PathMapper, RootMapping

pytestmark = pytest.mark.db
RADARR_ROOT = PurePosixPath("/radarr/movies")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tree(root: Path) -> list[tuple[str, int, int]]:
    """Every entry under ``root`` with its size and modification time, to show nothing moved."""
    if not root.exists():
        return []
    return sorted(
        (str(path.relative_to(root)), path.lstat().st_size, path.lstat().st_mtime_ns)
        for path in root.rglob("*")
    )


@pytest.fixture
def sessions(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(db_engine)


@pytest.fixture
def library(tmp_path: Path) -> Path:
    root = tmp_path / "movies"
    (root / "4K").mkdir(parents=True)
    return root


def _mapper(root: Path) -> PathMapper:
    return PathMapper([RootMapping(RADARR_ROOT, root)])


async def _movie(
    sessions: async_sessionmaker[AsyncSession], library: Path, radarr_id: int, title: str
) -> tuple[int, Path]:
    folder = library / "4K" / f"{title} (2000)"
    folder.mkdir()
    async with sessions() as session:
        movie = Movie(
            radarr_id=radarr_id,
            title=title,
            sort_title=title.lower(),
            year=2000,
            has_file=True,
            is_present=True,
            radarr_path=str(RADARR_ROOT / "4K" / f"{title} (2000)"),
            last_seen_at=datetime.now(UTC),
        )
        session.add(movie)
        await session.commit()
        return movie.id, folder


async def _deployment(
    sessions: async_sessionmaker[AsyncSession],
    movie_id: int,
    new: bytes | None,
    previous: bytes | None,
    *,
    action: str = "deploy",
    restores: int | None = None,
    by: str = "owner",
) -> int:
    async with sessions() as session:
        row = Deployment(
            movie_id=movie_id,
            artwork_kind="poster",
            action=action,
            initiated_by=by,
            file_name="poster.jpg",
            new_sha256=_sha(new) if new is not None else None,
            new_size_bytes=len(new) if new is not None else None,
            previous_sha256=_sha(previous) if previous is not None else None,
            previous_origin="library" if previous is not None else None,
            backup_file=f"posters/{movie_id}/{_sha(previous)}.jpg" if previous else None,
            restores_deployment_id=restores,
        )
        session.add(row)
        await session.commit()
        return row.id


def _keep_backup(backups: Path, movie_id: int, data: bytes) -> Path:
    path = backups / "posters" / str(movie_id) / f"{_sha(data)}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


async def _verify(
    sessions: async_sessionmaker[AsyncSession], library: Path, backups: Path
) -> verify.VerifyReport:
    async with sessions() as session:
        return await verify.verify_deployments(
            session, mapper=_mapper(library), backups_dir=backups
        )


async def test_everything_as_recorded(
    sessions: async_sessionmaker[AsyncSession], library: Path, tmp_path: Path
) -> None:
    backups = tmp_path / "backups"
    heat, heat_folder = await _movie(sessions, library, 1, "Heat")
    ronin, ronin_folder = await _movie(sessions, library, 2, "Ronin")
    # Heat: the owner's poster replaced by a deploy, then a second deploy.
    (heat_folder / "poster.jpg").write_bytes(b"second")
    _keep_backup(backups, heat, b"owners")
    _keep_backup(backups, heat, b"first")
    await _deployment(sessions, heat, b"first", b"owners")
    await _deployment(sessions, heat, b"second", b"first")
    # Ronin: deployed into an empty folder, then restored to no poster.
    _keep_backup(backups, ronin, b"ronin's")
    deployed = await _deployment(sessions, ronin, b"ronin's", None)
    await _deployment(sessions, ronin, None, b"ronin's", action="restore", restores=deployed)

    report = await _verify(sessions, library, backups)

    assert [(p.title, p.state) for p in report.posters] == [
        ("Heat (2000)", "as_recorded"),
        ("Ronin (2000)", "as_recorded"),
    ]
    assert [(b.backup_file.split("/")[1], b.state) for b in report.backups] == [
        (str(heat), "ok"),
        (str(heat), "ok"),
        (str(ronin), "ok"),
    ]
    assert report.ok
    assert report.counts() == {
        "posters": {"as_recorded": 2, "replaced": 0, "gone": 0, "unreadable": 0},
        "backups": {"ok": 3, "missing": 0, "damaged": 0},
    }


async def test_a_replaced_poster_a_vanished_one_and_a_damaged_backup_are_reported(
    sessions: async_sessionmaker[AsyncSession], library: Path, tmp_path: Path
) -> None:
    backups = tmp_path / "backups"
    replaced, replaced_folder = await _movie(sessions, library, 1, "Heat")
    gone, _ = await _movie(sessions, library, 2, "Ronin")
    damaged, damaged_folder = await _movie(sessions, library, 3, "Thief")
    missing, missing_folder = await _movie(sessions, library, 4, "Collateral")
    appeared, appeared_folder = await _movie(sessions, library, 5, "Manhunter")

    (replaced_folder / "poster.jpg").write_bytes(b"put there by hand")
    await _deployment(sessions, replaced, b"deployed", None)
    await _deployment(sessions, gone, b"deployed", None)  # and then deleted: no file
    (damaged_folder / "poster.jpg").write_bytes(b"deployed")
    _keep_backup(backups, damaged, b"owners").write_bytes(b"owners, damaged")
    await _deployment(sessions, damaged, b"deployed", b"owners")
    (missing_folder / "poster.jpg").write_bytes(b"deployed")
    await _deployment(sessions, missing, b"deployed", b"owners")  # its backup was never kept
    (appeared_folder / "poster.jpg").write_bytes(b"new")
    _keep_backup(backups, appeared, b"was")
    first = await _deployment(sessions, appeared, b"was", None)
    await _deployment(sessions, appeared, None, b"was", action="restore", restores=first)

    report = await _verify(sessions, library, backups)

    states = {p.movie_id: (p.state, p.found_sha256) for p in report.posters}
    assert states[replaced] == ("replaced", _sha(b"put there by hand"))
    assert states[gone] == ("gone", None)
    assert states[damaged] == ("as_recorded", _sha(b"deployed"))
    assert states[missing] == ("as_recorded", _sha(b"deployed"))
    assert states[appeared][0] == "replaced"
    by_movie = {b.movie_id: b for b in report.backups}
    assert by_movie[damaged].state == "damaged"
    assert by_movie[damaged].note == f"holds bytes with hash {_sha(b'owners, damaged')}"
    assert by_movie[missing].state == "missing"
    assert by_movie[appeared].state == "ok"
    assert not report.ok


async def test_a_folder_that_cannot_be_read_is_not_called_gone(
    sessions: async_sessionmaker[AsyncSession], library: Path, tmp_path: Path
) -> None:
    movie, folder = await _movie(sessions, library, 1, "Heat")
    await _deployment(sessions, movie, b"deployed", None)
    folder.rmdir()  # as an unmounted share looks

    report = await _verify(sessions, library, tmp_path / "backups")

    assert [(p.state, p.found_sha256) for p in report.posters] == [("unreadable", None)]
    assert "is the share mounted?" in (report.posters[0].note or "")
    assert report.ok  # no backup is named, so none is missing


async def test_a_symbolic_link_in_place_of_a_poster_or_a_backup_is_not_followed(
    sessions: async_sessionmaker[AsyncSession], library: Path, tmp_path: Path
) -> None:
    backups = tmp_path / "backups"
    movie, folder = await _movie(sessions, library, 1, "Heat")
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"deployed")
    (folder / "poster.jpg").symlink_to(outside)
    target = backups / "posters" / str(movie) / f"{_sha(b'owners')}.jpg"
    target.parent.mkdir(parents=True)
    (tmp_path / "owners.jpg").write_bytes(b"owners")
    target.symlink_to(tmp_path / "owners.jpg")
    await _deployment(sessions, movie, b"deployed", b"owners")

    report = await _verify(sessions, library, backups)

    assert report.posters[0].state == "replaced"
    assert report.posters[0].note == "poster.jpg is not a regular file"
    assert report.backups[0].state == "damaged"
    assert not report.ok


async def test_it_writes_nothing(
    sessions: async_sessionmaker[AsyncSession], library: Path, tmp_path: Path
) -> None:
    backups = tmp_path / "backups"  # never created: verifying must not create it
    movie, folder = await _movie(sessions, library, 1, "Heat")
    (folder / "poster.jpg").write_bytes(b"deployed")
    await _deployment(sessions, movie, b"deployed", b"owners")
    before = _tree(tmp_path)

    report = await _verify(sessions, library, backups)

    assert report.backups[0].state == "missing"
    assert _tree(tmp_path) == before
    assert not backups.exists()


@pytest.fixture
def cli_database(
    monkeypatch: pytest.MonkeyPatch, database_url: str, library: Path, tmp_path: Path
) -> Iterator[None]:
    parsed = make_url(database_url)
    monkeypatch.setattr(settings, "POSTGRES_USER", parsed.username)
    monkeypatch.setattr(settings, "POSTGRES_PASSWORD", SecretStr(parsed.password or ""))
    monkeypatch.setattr(settings, "POSTGRES_HOST", parsed.host)
    monkeypatch.setattr(settings, "POSTGRES_PORT", parsed.port)
    monkeypatch.setattr(settings, "POSTGRES_DB", parsed.database)
    monkeypatch.setattr(settings, "MOVIE_ROOTS", [library])
    monkeypatch.setattr(settings, "RADARR_MOVIE_ROOTS", [RADARR_ROOT])
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    yield


async def test_the_command_exits_1_only_for_a_missing_or_damaged_backup(
    cli_database: None,
    sessions: async_sessionmaker[AsyncSession],
    library: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import asyncio

    movie, folder = await _movie(sessions, library, 1, "Heat")
    (folder / "poster.jpg").write_bytes(b"put there by hand")
    await _deployment(sessions, movie, b"deployed", b"owners")
    backup = _keep_backup(settings.backups_dir, movie, b"owners")

    code = await asyncio.to_thread(cli.main, ["deployments", "verify"])
    out = capsys.readouterr().out
    assert code == 0
    assert "replaced since" in out and "Heat (2000)" in out
    assert out.splitlines()[-1] == "1 backup(s): 1 ok, 0 missing, 0 damaged"

    os.remove(backup)
    code = await asyncio.to_thread(cli.main, ["deployments", "verify", "--json"])
    printed = json.loads(capsys.readouterr().out)
    assert code == 1
    assert printed["ok"] is False
    assert printed["counts"]["backups"] == {"ok": 0, "missing": 1, "damaged": 0}
    assert printed["posters"][0]["state"] == "replaced"
