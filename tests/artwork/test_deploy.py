"""The poster write path, against a real PostgreSQL server and a folder in tmp_path."""

from __future__ import annotations

import io
import os
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import httpx
import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from marquee.artwork import backup, deploy
from marquee.config import settings
from marquee.db.engine import create_session_factory
from marquee.db.models import Artwork, Deployment, Movie
from marquee.library.sync import stat_poster


def jpeg(
    width: int = 500, height: int = 750, colour: tuple[int, int, int] = (200, 30, 30)
) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(out, format="JPEG")
    return out.getvalue()


OLD = jpeg(colour=(10, 10, 10))
NEW = jpeg(colour=(250, 250, 0))


# ── The new bytes ─────────────────────────────────────────────────────────


def test_a_good_jpeg_passes() -> None:
    assert deploy.validate_jpeg(jpeg(600, 900)) == (600, 900)


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (600, 900)).save(out, format="PNG")
    return out.getvalue()


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (_png(), "PNG image, not a JPEG"),
        (jpeg(400, 900), "400x900"),
        (jpeg(600, 10_001), "600x10001"),
        (jpeg(600, 900)[:2000], "do not decode"),
        (b"<html>not an image</html>", "do not decode"),
    ],
)
def test_anything_else_is_not_a_jpeg(data: bytes, message: str) -> None:
    with pytest.raises(deploy.NotAJpegError, match=message):
        deploy.validate_jpeg(data)


def test_a_cut_off_jpeg_is_refused_even_where_the_engine_tolerates_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PIL import ImageFile

    monkeypatch.setattr(ImageFile, "LOAD_TRUNCATED_IMAGES", True)
    with pytest.raises(deploy.NotAJpegError, match="do not decode"):
        deploy.validate_jpeg(jpeg(600, 900)[:2000])
    assert ImageFile.LOAD_TRUNCATED_IMAGES is True


def test_the_original_is_asked_for_as_a_jpg() -> None:
    assert deploy.original_url("abc.jpg") == "https://image.tmdb.org/t/p/original/abc.jpg"
    assert deploy.original_url("abc.png") == "https://image.tmdb.org/t/p/original/abc.jpg"
    for bad in ("../abc.jpg", "a/b.jpg", ""):
        with pytest.raises(deploy.DeployError):
            deploy.original_url(bad)


def _client(
    content: bytes, content_type: str = "image/jpeg", status: int = 200
) -> httpx.AsyncClient:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(status, content=content, headers={"content-type": content_type})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client.seen = seen  # type: ignore[attr-defined]
    return client


async def test_fetch_original_returns_a_checked_jpeg() -> None:
    client = _client(NEW)
    assert await deploy.fetch_original("x.png", client) == NEW
    assert client.seen == ["https://image.tmdb.org/t/p/original/x.jpg"]  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("content", "content_type", "message"),
    [
        (b"<html></html>", "text/html", "not a poster"),
        (_png(), "image/png", "not a JPEG"),
        (jpeg(300, 300), "image/jpeg", "300x300"),
    ],
)
async def test_fetch_original_refuses_what_is_not_a_usable_jpeg(
    content: bytes, content_type: str, message: str
) -> None:
    with pytest.raises(deploy.NotAJpegError, match=message):
        await deploy.fetch_original("x.jpg", _client(content, content_type))


async def test_a_network_failure_is_left_to_the_retry_rule() -> None:
    with pytest.raises(httpx.HTTPStatusError) as raised:
        await deploy.fetch_original("x.jpg", _client(b"", status=503))
    assert not isinstance(raised.value, deploy.DeployError)


# ── The write ─────────────────────────────────────────────────────────────

RADARR_ROOT = PurePosixPath("/radarr/movies")


@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "movies"
    (root / "1080p" / "Heat (1995)").mkdir(parents=True)
    (root / "1080p" / "Heat (1995)" / "Heat (1995) - [1080p].mkv").write_bytes(b"")
    monkeypatch.setattr(settings, "MOVIE_ROOTS", [root])
    monkeypatch.setattr(settings, "RADARR_MOVIE_ROOTS", [RADARR_ROOT])
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    return root / "1080p" / "Heat (1995)"


@pytest.fixture
def sessions(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(db_engine)


async def _movie(sessions: async_sessionmaker[AsyncSession]) -> Movie:
    now = datetime.now(UTC)
    async with sessions() as session:
        movie = Movie(
            radarr_id=1,
            title="Heat",
            sort_title="heat",
            year=1995,
            radarr_path=str(RADARR_ROOT / "1080p" / "Heat (1995)"),
            has_file=True,
            is_present=True,
            last_seen_at=now,
        )
        session.add(movie)
        await session.commit()
        return movie


async def _library_row(
    sessions: async_sessionmaker[AsyncSession], movie: Movie, poster: Path
) -> None:
    found = stat_poster(poster)
    assert found is not None
    now = datetime.now(UTC)
    async with sessions() as session:
        session.add(
            Artwork(
                movie_id=movie.id,
                artwork_kind="poster",
                file_name="poster.jpg",
                sha256=backup.sha256_of(poster.read_bytes()),
                size_bytes=found.size_bytes,
                file_modified_at=found.modified_at,
                origin="library",
                first_seen_at=now,
                last_seen_at=now,
            )
        )
        await session.commit()


async def _state(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[list[Artwork], list[Deployment]]:
    async with sessions() as session:
        rows = list(await session.scalars(select(Artwork)))
        deployments = list(await session.scalars(select(Deployment).order_by(Deployment.id)))
    return rows, deployments


def _deploy(
    movie: Movie, data: bytes | None = NEW, job_id: int | None = None, **values: object
) -> deploy.Write:
    defaults: dict[str, object] = {
        "movie": movie,
        "new": data,
        "new_origin": "marquee",
        "action": "deploy",
        "initiated_by": "owner",
        "job_id": job_id,
        "source_file": "new.jpg",
    }
    return deploy.Write(**{**defaults, **values})  # type: ignore[arg-type]


@pytest.mark.db
async def test_a_deploy_backs_up_writes_verifies_and_records(
    library: Path, sessions: async_sessionmaker[AsyncSession]
) -> None:
    poster = library / "poster.jpg"
    poster.write_bytes(OLD)
    os.chmod(poster, 0o640)
    movie = await _movie(sessions)
    await _library_row(sessions, movie, poster)

    result = await deploy.write_poster(sessions, _deploy(movie))

    assert result.outcome == "written"
    assert poster.read_bytes() == NEW
    assert oct(poster.stat().st_mode & 0o777) == oct(0o640)  # the replaced file's mode is kept
    assert result.backup_file == backup.backup_key(movie.id, backup.sha256_of(OLD))
    assert (settings.backups_dir / result.backup_file).read_bytes() == OLD
    assert not [p.name for p in library.iterdir() if p.name.startswith(".marquee-")]
    (row,), (deployment,) = await _state(sessions)
    assert (row.origin, row.sha256, row.size_bytes) == ("marquee", backup.sha256_of(NEW), len(NEW))
    assert row.file_modified_at == stat_poster(poster).modified_at  # type: ignore[union-attr]
    assert (deployment.action, deployment.new_sha256, deployment.new_size_bytes) == (
        "deploy",
        backup.sha256_of(NEW),
        len(NEW),
    )
    assert (deployment.previous_sha256, deployment.previous_origin) == (
        backup.sha256_of(OLD),
        "library",
    )
    assert deployment.backup_file == result.backup_file


@pytest.mark.db
async def test_a_deploy_into_an_empty_folder_backs_up_nothing(
    library: Path, sessions: async_sessionmaker[AsyncSession]
) -> None:
    movie = await _movie(sessions)

    result = await deploy.write_poster(sessions, _deploy(movie))

    assert (result.outcome, result.backup_file, result.previous_sha256) == ("written", None, None)
    assert oct((library / "poster.jpg").stat().st_mode & 0o777) == oct(deploy.NEW_FILE_MODE)
    (row,), (deployment,) = await _state(sessions)
    assert row.origin == "marquee" and deployment.previous_origin is None


@pytest.mark.db
async def test_the_same_write_again_adds_nothing(
    library: Path, sessions: async_sessionmaker[AsyncSession]
) -> None:
    movie = await _movie(sessions)
    first = await deploy.write_poster(sessions, _deploy(movie))

    again = await deploy.write_poster(sessions, _deploy(movie))

    assert again.outcome == "already_deployed" and again.deployment_id == first.deployment_id
    assert len((await _state(sessions))[1]) == 1


@pytest.mark.db
async def test_a_job_that_already_recorded_its_write_is_done(
    library: Path, sessions: async_sessionmaker[AsyncSession]
) -> None:
    movie = await _movie(sessions)
    async with sessions() as session:
        await session.execute(
            Deployment.__table__.insert().values(  # noqa: SLF001
                movie_id=movie.id,
                artwork_kind="poster",
                action="deploy",
                initiated_by="owner",
                file_name="poster.jpg",
                new_sha256="a" * 64,
                new_size_bytes=1,
            )
        )
        await session.commit()
    from sqlalchemy import text

    async with sessions() as session:
        await session.execute(
            text(
                "insert into jobs (id, kind, job_class, state) values (5, 'poster_deploy', 'media_write', 'running')"
            )
        )
        await session.execute(text("update deployments set job_id = 5"))
        await session.commit()

    result = await deploy.write_poster(sessions, _deploy(movie, job_id=5))

    assert result.outcome == "already_recorded"
    assert not (library / "poster.jpg").exists()


@pytest.mark.db
async def test_a_file_an_earlier_attempt_renamed_is_recorded_without_writing(
    library: Path, sessions: async_sessionmaker[AsyncSession]
) -> None:
    poster = library / "poster.jpg"
    poster.write_bytes(OLD)
    movie = await _movie(sessions)
    await _library_row(sessions, movie, poster)
    # What an attempt killed between the rename and the commit leaves: the backup made,
    # the new file in place, and the database still describing the old poster.
    backup.store(OLD, movie.id)
    poster.write_bytes(NEW)
    mtime = poster.stat().st_mtime_ns

    result = await deploy.write_poster(sessions, _deploy(movie))

    assert result.outcome == "already_in_place"
    assert poster.stat().st_mtime_ns == mtime
    (row,), (deployment,) = await _state(sessions)
    assert (row.origin, row.sha256) == ("marquee", backup.sha256_of(NEW))
    assert deployment.previous_sha256 == backup.sha256_of(OLD)
    assert deployment.backup_file == backup.backup_key(movie.id, backup.sha256_of(OLD))


@pytest.mark.db
async def test_a_candidate_identical_to_the_owners_poster_leaves_it_the_owners(
    library: Path, sessions: async_sessionmaker[AsyncSession]
) -> None:
    # The owner's poster.jpg is byte for byte the TMDB file the deploy fetched (the owner
    # downloaded it from TMDB). Nothing is written, the poster stays the owner's, and undoing
    # the deployment must leave it in place, not remove it.
    poster = library / "poster.jpg"
    poster.write_bytes(NEW)
    movie = await _movie(sessions)
    await _library_row(sessions, movie, poster)

    result = await deploy.write_poster(sessions, _deploy(movie))

    assert result.outcome == "already_in_place"
    assert (result.previous_sha256, result.previous_origin) == (backup.sha256_of(NEW), "library")
    assert result.backup_file == backup.backup_key(movie.id, backup.sha256_of(NEW))
    (row,), (deployment,) = await _state(sessions)
    assert row.origin == "library"
    assert deployment.previous_sha256 == backup.sha256_of(NEW)

    undone = await deploy.write_poster(
        sessions,
        _deploy(
            movie,
            backup.verify(deployment.backup_file, deployment.previous_sha256),  # type: ignore[arg-type]
            action="restore",
            new_origin=deployment.previous_origin,
            restores_deployment_id=deployment.id,
            source_file=None,
        ),
    )
    assert poster.read_bytes() == NEW
    assert undone.outcome == "already_in_place"
    (row,), _ = await _state(sessions)
    assert row.origin == "library"


@pytest.mark.db
async def test_a_file_in_place_without_its_backup_is_refused(
    library: Path, sessions: async_sessionmaker[AsyncSession]
) -> None:
    poster = library / "poster.jpg"
    poster.write_bytes(OLD)
    movie = await _movie(sessions)
    await _library_row(sessions, movie, poster)
    poster.write_bytes(NEW)

    with pytest.raises(deploy.DeployError, match="backup of the poster the database describes"):
        await deploy.write_poster(sessions, _deploy(movie))
    assert (await _state(sessions))[1] == []


@pytest.mark.db
async def test_a_guard_can_skip_the_write(
    library: Path, sessions: async_sessionmaker[AsyncSession]
) -> None:
    (library / "poster.jpg").write_bytes(OLD)
    movie = await _movie(sessions)

    result = await deploy.write_poster(sessions, _deploy(movie), guard=lambda current, row: "no")

    assert (result.outcome, result.note) == ("skipped", "no")
    assert (library / "poster.jpg").read_bytes() == OLD
    assert (await _state(sessions))[1] == []
    assert backup.summary() == (0, 0)


@pytest.mark.db
async def test_a_write_that_does_not_verify_puts_the_old_poster_back(
    library: Path, sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    poster = library / "poster.jpg"
    poster.write_bytes(OLD)
    movie = await _movie(sessions)
    real_write = deploy.write_file
    calls: list[bytes] = []

    def corrupting_write(target: deploy.Target, data: bytes, mode: int) -> None:
        calls.append(data)
        real_write(target, data[:-10] if len(calls) == 1 else data, mode)

    monkeypatch.setattr(deploy, "write_file", corrupting_write)

    with pytest.raises(deploy.VerificationError, match="put back"):
        await deploy.write_poster(sessions, _deploy(movie))
    assert poster.read_bytes() == OLD
    assert (await _state(sessions))[1] == []


@pytest.mark.db
async def test_a_failed_rename_leaves_no_temporary_file(
    library: Path, sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    movie = await _movie(sessions)

    def refuse(self: object, staged: object, destination: object) -> None:
        raise OSError("the share went away")

    monkeypatch.setattr("marquee.fs.boundary.FilesystemBoundary.atomic_replace", refuse)
    with pytest.raises(OSError, match="share went away"):
        await deploy.write_poster(sessions, _deploy(movie))
    assert sorted(p.name for p in library.iterdir()) == ["Heat (1995) - [1080p].mkv"]


@pytest.mark.db
async def test_a_missing_folder_is_never_created(
    library: Path, sessions: async_sessionmaker[AsyncSession]
) -> None:
    movie = await _movie(sessions)
    for child in library.iterdir():
        child.unlink()
    library.rmdir()

    with pytest.raises(deploy.DeployError, match="never creates one"):
        await deploy.write_poster(sessions, _deploy(movie))
    assert not library.exists()


@pytest.mark.db
async def test_a_poster_that_is_a_symlink_is_left_alone(
    library: Path, sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere.jpg"
    elsewhere.write_bytes(OLD)
    (library / "poster.jpg").symlink_to(elsewhere)
    movie = await _movie(sessions)

    with pytest.raises(deploy.DeployError, match="symbolic link"):
        await deploy.write_poster(sessions, _deploy(movie))
    assert elsewhere.read_bytes() == OLD and (library / "poster.jpg").is_symlink()


@pytest.mark.db
async def test_a_restore_of_no_poster_backs_up_then_removes(
    library: Path, sessions: async_sessionmaker[AsyncSession]
) -> None:
    movie = await _movie(sessions)
    deployed = await deploy.write_poster(sessions, _deploy(movie))

    result = await deploy.write_poster(
        sessions,
        _deploy(
            movie,
            None,
            action="restore",
            new_origin="library",
            restores_deployment_id=deployed.deployment_id,
            source_file=None,
        ),
    )

    assert result.outcome == "removed"
    assert not (library / "poster.jpg").exists()
    assert result.backup_file == backup.backup_key(movie.id, backup.sha256_of(NEW))
    rows, deployments = await _state(sessions)
    assert rows == []
    assert (deployments[1].new_sha256, deployments[1].previous_sha256) == (
        None,
        backup.sha256_of(NEW),
    )
