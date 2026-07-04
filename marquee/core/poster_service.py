"""PosterService — the single path that writes a poster to a media folder.

Both deployment (pipeline auto-deploy, feedback approve/override) and
restoration (Radarr upgrade webhook, self-heal scan) go through here, so
filename rendering, path validation, atomic writes, cache population, DB
state, and the artwork-events audit trail can never drift apart.

Cache layout (design 10 §12):
    data/cache/posters/movies/{tmdb_id}.jpg          ← exact deployed bytes
    data/cache/posters/movies/{tmdb_id}.meta.json    ← provenance for fallback
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
import imagehash
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.core.download_guard import ensure_image_response
from marquee.core.path_utils import PathValidationError, safe_translate_and_validate
from marquee.core.poster_subjects import PosterSubject
from marquee.models import ArtworkEvent, Movie, Season, Series

logger = logging.getLogger(__name__)

_TMDB_ORIGINAL = "https://image.tmdb.org/t/p/original"
_RESTORE_CHAINS = {
    "local": ("local", "cache", "download"),
    "download": ("cache", "download", "local"),
}


@dataclass
class DeployResult:
    deployed_path: str
    cache_path: str
    sha256: str
    backup_path: str = ""


@dataclass
class RestoreResult:
    restored: bool
    source: str  # "local" | "cache" | "download" | "none"
    path: str | None = None
    error: str | None = None


def sanitize_poster_filename(name: str) -> str:
    """Validate a rendered poster filename down to one safe path component.

    The format string is UI-configurable and the basename substitution comes
    from Radarr, so both are treated as untrusted: no separators, no ``..``,
    no hidden/tmp-style leading dot, and the extension is normalized to
    ``.jpg`` (``.jpeg`` accepted). Raises PathValidationError.
    """
    candidate = (name or "").strip()
    if not candidate or "\x00" in candidate:
        raise PathValidationError(f"Invalid poster filename: {name!r}")
    if "/" in candidate or "\\" in candidate:
        raise PathValidationError(f"Poster filename must not contain path separators: {name!r}")
    if candidate.startswith(".") or Path(candidate).name != candidate:
        raise PathValidationError(f"Poster filename must be a bare filename: {name!r}")
    if Path(candidate).suffix.lower() not in (".jpg", ".jpeg"):
        candidate += ".jpg"
    return candidate


def render_filename(movie: Movie) -> str:
    """Render the configured movie poster filename (handles {movie_basename})."""
    return PosterSubject.from_movie(movie).render_filename()


def _confine_dest(folder: Path, filename: str) -> Path:
    """Join folder/filename and re-verify the result stays inside folder."""
    dest = folder / filename
    if dest.resolve().parent != folder.resolve():
        raise PathValidationError(f"Poster destination escapes movie folder: {dest}")
    return dest


def _cache_dir() -> Path:
    return settings.poster_cache_path / "movies"


def cache_paths(tmdb_id: int) -> tuple[Path, Path]:
    base = _cache_dir()
    return base / f"{tmdb_id}.jpg", base / f"{tmdb_id}.meta.json"


def backup_path(movie: Movie) -> Path:
    if movie.poster_local_backup_path:
        return Path(movie.poster_local_backup_path)
    return settings.poster_backup_path / f"{movie.id}.jpg"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _phash(path: Path) -> str | None:
    try:
        with Image.open(path) as image:
            return str(imagehash.phash(image))
    except Exception as exc:  # noqa: BLE001 — phash is advisory
        logger.warning("phash failed for %s: %s", path, exc)
        return None


def _atomic_copy(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not os.access(dest.parent, os.W_OK):
        import pwd  # noqa: PLC0415

        try:
            user = pwd.getpwuid(os.geteuid()).pw_name
        except Exception:
            user = str(os.geteuid())
        raise PermissionError(
            f"Cannot write to folder {dest.parent} — "
            f"the backend runs as user '{user}' but this directory is not "
            f'group-writable. Run: chmod g+w "{dest.parent}" '
            f"(or add '{user}' to the owning group)."
        )
    # If the destination file already exists and we can't overwrite it,
    # try to make it writable first.
    if dest.exists() and not os.access(dest, os.W_OK):
        with contextlib.suppress(OSError):
            dest.chmod(0o664)
    tmp = dest.parent / f".{dest.name}.tmp"
    shutil.copy2(source, tmp)
    try:
        os.replace(tmp, dest)
    except PermissionError:
        raise PermissionError(
            f"Cannot write poster to {dest} — the backend lacks write permission "
            f'on the folder. Run: chmod g+w "{dest.parent}"'
        ) from None


def _atomic_write_bytes(data: bytes, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".{dest.name}.tmp"
    tmp.write_bytes(data)
    os.replace(tmp, dest)


def _populate_cache_sync(dest: Path, cache_file: Path, cache_meta: Path, meta: dict) -> None:
    """Copy deployed bytes + provenance sidecar into the poster cache."""
    _atomic_copy(dest, cache_file)
    with Image.open(dest) as image:
        width, height = image.size
    cache_meta.write_text(
        json.dumps({**meta, "width": width, "height": height}, indent=2),
        encoding="utf-8",
    )


async def _log_event(
    db: AsyncSession, subject: PosterSubject, action: str, source: str, detail: dict
) -> None:
    db.add(
        ArtworkEvent(
            **subject.event_fk_kwargs(),
            action=action,
            source=source,
            detail=json.dumps(detail, default=str),
        )
    )


class PosterService:
    """Deploy + restore posters through one validated, audited path."""

    async def deploy(
        self,
        db: AsyncSession,
        subject: PosterSubject | Movie | Series | Season,
        source_file: Path,
        *,
        source: str = "pipeline",
        ai_selected: bool = True,
        user_approved: bool = False,
        poster_source: str | None = "tmdb",
        poster_source_url: str | None = None,
    ) -> DeployResult:
        """Write ``source_file`` into the subject's folder + cache + DB + event."""
        if not isinstance(subject, PosterSubject):
            if isinstance(subject, Movie):
                subject = PosterSubject.from_movie(subject)
            elif isinstance(subject, Series):
                subject = PosterSubject.from_series(subject)
            elif isinstance(subject, Season):
                series = (await db.execute(select(Series).where(Series.id == subject.series_id))).scalar_one()
                subject = PosterSubject.from_season(subject, series)

        if not source_file.is_file():
            raise FileNotFoundError(f"Poster source file missing: {source_file}")

        filename = subject.render_filename()
        folder = safe_translate_and_validate(subject.folder_raw, source=subject.path_source)
        if not await asyncio.to_thread(folder.is_dir):
            raise PathValidationError(f"Folder does not exist: {folder}")
        dest = _confine_dest(folder, filename)

        await asyncio.to_thread(_atomic_copy, source_file, dest)

        sha256 = await asyncio.to_thread(_sha256, dest)
        phash = await asyncio.to_thread(_phash, dest)

        # Cache exact deployed bytes + provenance sidecar.
        cache_file = cache_meta = None
        if subject.tmdb_id is not None:
            cpaths = subject.cache_paths()
            if cpaths:
                cache_file, cache_meta = cpaths
                await asyncio.to_thread(
                    _populate_cache_sync,
                    dest,
                    cache_file,
                    cache_meta,
                    {
                        "source": poster_source,
                        "source_url": poster_source_url,
                        "sha256": sha256,
                        "phash": phash,
                        "deployed_filename": filename,
                        "deployed_at": datetime.now(UTC).isoformat(),
                    },
                )

        backup_file = subject.backup_file()
        backup_error = None
        try:
            await asyncio.to_thread(_atomic_copy, dest, backup_file)
            subject.entity.poster_local_backup_path = str(backup_file)
        except Exception as exc:  # noqa: BLE001 — backup must not fail deployment
            backup_error = str(exc)
            logger.warning("POSTER BACKUP FAILED | subject=%s | %s", subject.title, exc)

        subject.entity.poster_path = str(dest)
        subject.entity.poster_source = poster_source
        if poster_source_url:
            subject.entity.poster_source_url = poster_source_url
        subject.entity.poster_ai_selected = ai_selected
        subject.entity.poster_user_approved = user_approved
        subject.entity.poster_deployed_filename = filename
        subject.entity.poster_deployed_at = datetime.now(UTC)
        subject.entity.poster_sha256 = sha256
        if phash:
            subject.entity.poster_phash = phash

        await _log_event(
            db,
            subject,
            "deploy",
            source,
            {
                "path": str(dest),
                "cache": str(cache_file) if cache_file else None,
                "backup": str(backup_file) if backup_error is None else None,
                "backup_error": backup_error,
                "user_approved": user_approved,
            },
        )
        await db.commit()

        logger.info("POSTER DEPLOYED | subject=%s | path=%s | source=%s", subject.title, dest, source)
        return DeployResult(
            deployed_path=str(dest),
            cache_path=str(cache_file) if cache_file else "",
            sha256=sha256,
            backup_path=str(backup_file) if backup_error is None else "",
        )

    async def restore(
        self,
        db: AsyncSession,
        subject: PosterSubject | Movie | Series | Season,
        *,
        new_folder: str | None = None,
        source: str = "webhook",
    ) -> RestoreResult:
        """Restore the deployed poster to the (new) subject folder."""
        if not isinstance(subject, PosterSubject):
            if isinstance(subject, Movie):
                subject = PosterSubject.from_movie(subject)
            elif isinstance(subject, Series):
                subject = PosterSubject.from_series(subject)
            elif isinstance(subject, Season):
                series = (await db.execute(select(Series).where(Series.id == subject.series_id))).scalar_one()
                subject = PosterSubject.from_season(subject, series)

        folder_raw = subject.folder_raw if subject.media_type != "movie" else (new_folder or subject.folder_raw)
        try:
            folder = safe_translate_and_validate(folder_raw, source=subject.path_source)
        except PathValidationError as exc:
            await _log_event(db, subject, "restore_failed", source, {"error": str(exc)})
            await db.commit()
            return RestoreResult(restored=False, source="none", error=str(exc))

        try:
            filename = (
                sanitize_poster_filename(subject.entity.poster_deployed_filename)
                if subject.entity.poster_deployed_filename
                else subject.render_filename()
            )
            dest = _confine_dest(folder, filename)
        except PathValidationError as exc:
            await _log_event(db, subject, "restore_failed", source, {"error": str(exc)})
            await db.commit()
            return RestoreResult(restored=False, source="none", error=str(exc))

        errors: list[str] = []
        cache_file = None
        if subject.tmdb_id is not None:
            cpaths = subject.cache_paths()
            if cpaths:
                cache_file, _ = cpaths

        for candidate in _RESTORE_CHAINS.get(
            settings.POSTER_RESTORE_METHOD, _RESTORE_CHAINS["download"]
        ):
            if candidate == "local":
                local_file = subject.backup_file()
                if not await asyncio.to_thread(local_file.is_file):
                    errors.append(f"local missing: {local_file}")
                    continue
                try:
                    if (
                        subject.entity.poster_sha256
                        and await asyncio.to_thread(_sha256, local_file) != subject.entity.poster_sha256
                    ):
                        logger.warning(
                            "RESTORE | local backup sha mismatch for %s — using it anyway",
                            subject.title,
                        )
                    await asyncio.to_thread(_atomic_copy, local_file, dest)
                    await self._finalize_restore(db, subject, dest, folder_raw, source, "local")
                    return RestoreResult(restored=True, source="local", path=str(dest))
                except OSError as exc:
                    errors.append(f"local failed: {exc}")
                    logger.warning("RESTORE | local backup copy failed (%s)", exc)
                    continue

            if candidate == "cache":
                if not cache_file or not await asyncio.to_thread(cache_file.is_file):
                    errors.append("cache missing")
                    continue
                try:
                    if (
                        subject.entity.poster_sha256
                        and await asyncio.to_thread(_sha256, cache_file) != subject.entity.poster_sha256
                    ):
                        logger.warning(
                            "RESTORE | cache sha mismatch for %s — using it anyway",
                            subject.title,
                        )
                    await asyncio.to_thread(_atomic_copy, cache_file, dest)
                    await self._finalize_restore(db, subject, dest, folder_raw, source, "cache")
                    return RestoreResult(restored=True, source="cache", path=str(dest))
                except OSError as exc:
                    errors.append(f"cache failed: {exc}")
                    logger.warning("RESTORE | cache copy failed (%s)", exc)
                    continue

            if candidate == "download":
                if not subject.entity.poster_source_url:
                    errors.append("download missing source URL")
                    continue
                try:
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        response = await client.get(subject.entity.poster_source_url)
                        response.raise_for_status()
                        ensure_image_response(response)
                    await asyncio.to_thread(_atomic_write_bytes, response.content, dest)
                    await self._finalize_restore(db, subject, dest, folder_raw, source, "download")
                    return RestoreResult(restored=True, source="download", path=str(dest))
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"download failed: {exc}")
                    continue

        subject.entity.poster_path = None  # flag for re-pipeline
        error = "; ".join(errors) if errors else "no restore sources available"
        await _log_event(db, subject, "restore_failed", source, {"error": error, "errors": errors})
        await db.commit()
        logger.error("POSTER RESTORE FAILED | subject=%s | %s", subject.title, error)
        return RestoreResult(restored=False, source="none", error=error)

    async def _finalize_restore(
        self, db, subject: PosterSubject, dest: Path, folder_raw: str, source: str, via: str
    ) -> None:
        subject.entity.poster_path = str(dest)
        if subject.media_type == "movie":
            subject.entity.folder_path = folder_raw
        subject.entity.poster_deployed_at = datetime.now(UTC)
        await _log_event(
            db,
            subject,
            "restore" if source != "heal" else "heal_restore",
            source,
            {"path": str(dest), "via": via},
        )
        await db.commit()
        logger.info("POSTER RESTORED | subject=%s | via=%s | path=%s", subject.title, via, dest)


poster_service = PosterService()


def tmdb_original_url(orig_filename: str) -> str:
    """Reconstruct the TMDB original-size URL from a candidate filename."""
    return f"{_TMDB_ORIGINAL}/{orig_filename.lstrip('/')}"
