"""LetterboxService — the single validated path that mutates an MKV file.

Applying or removing pixel-crop tags is the only place this feature touches the
user's media, so — exactly like ``PosterService`` for posters — it all funnels
through here: path validation, eligibility re-check, the ``mkvpropedit`` write,
a verification read, ``LetterboxState`` update, and a ``LetterboxEvent`` audit
row. Detection never writes; only this service does.

Eligibility (design §20): the resolved media file must be inside a configured
media root, be a regular writable ``.mkv`` with a video track. Everything else
is reported ``ineligible`` and never touched.

Tag-read caveat: whether ``mkvmerge -J`` surfaces existing pixel-crop in track
``properties`` varies by mkvtoolnix version (flagged for runtime verification).
The DB is therefore the source of truth for "what we applied"; the read-back is
best-effort verification + drift detection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.path_utils import PathValidationError, safe_translate_and_validate
from marquee.media import binaries
from marquee.models import LetterboxEvent, LetterboxState, Movie

logger = logging.getLogger(__name__)

# Per-file locks so two writers never touch one file concurrently.
_file_locks: dict[str, asyncio.Lock] = {}


class IneligibleError(Exception):
    """The movie's file cannot be tagged (not MKV / read-only / no video)."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass
class Eligibility:
    eligible: bool
    reason: str | None
    path: Path | None


@dataclass
class ApplyResult:
    applied: bool
    top: int
    bottom: int
    path: str
    verified: bool


@dataclass
class RemoveResult:
    removed: bool
    path: str


def _resolve_media_file(movie: Movie) -> Path:
    """Validated absolute path to the movie's video file.

    Raises ``PathValidationError`` if the folder is out of bounds, the file
    record is missing, or the joined path escapes the validated folder.
    """
    if not movie.movie_file_path:
        raise PathValidationError("Movie has no media file path (run sync)")
    folder = safe_translate_and_validate(movie.folder_path, source="radarr")
    candidate = (folder / movie.movie_file_path).resolve()
    # Re-confirm containment after the join (defends against ``..`` in the
    # relative path the API gave us).
    if not str(candidate).startswith(str(folder)):
        raise PathValidationError(f"Media file escapes its folder: {candidate}")
    return candidate


def _file_lock(path: str) -> asyncio.Lock:
    return _file_locks.setdefault(path, asyncio.Lock())


def read_applied_crop(path: Path | str) -> tuple[int, int] | None:
    """Best-effort read of currently-applied top/bottom pixel-crop via mkvmerge.

    Returns (top, bottom), (0, 0) if confirmed absent, or None if the tool
    couldn't report crop on this build (caller treats None as "unknown").
    """
    if binaries.resolve("mkvmerge") is None:
        return None
    result = binaries.run("mkvmerge", ["-J", "--", binaries.safe_media_path(path)], timeout=30.0)
    if not result.ok:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    video = next(
        (t for t in data.get("tracks", []) if t.get("type") == "video"), None
    )
    if video is None:
        return None
    props = video.get("properties", {})
    crop_keys = {k: v for k, v in props.items() if "crop" in k.lower()}
    if not crop_keys:
        # No crop reported. Cannot distinguish "absent" from "unsupported";
        # treat as unknown so we never wrongly claim a file is untagged.
        return None
    top = int(crop_keys.get("pixel_crop_top", 0) or 0)
    bottom = int(crop_keys.get("pixel_crop_bottom", 0) or 0)
    return top, bottom


class LetterboxService:
    """Apply / remove MKV crop tags through one validated, audited path."""

    def check_eligibility(self, movie: Movie) -> Eligibility:
        """Validate that *movie*'s file can be tagged (design §20)."""
        try:
            path = _resolve_media_file(movie)
        except PathValidationError as exc:
            return Eligibility(False, f"path_invalid: {exc}", None)

        if path.suffix.lower() != ".mkv":
            return Eligibility(False, "not_mkv", path)
        if not path.is_file():
            return Eligibility(False, "missing", path)
        if not os.access(path, os.W_OK):
            return Eligibility(False, "read_only", path)

        # Confirm it's a Matroska container with a video track.
        if binaries.resolve("mkvmerge") is not None:
            result = binaries.run("mkvmerge", ["-J", "--", binaries.safe_media_path(path)], timeout=30.0)
            if result.ok:
                try:
                    data = json.loads(result.stdout)
                except json.JSONDecodeError:
                    data = {}
                container = (data.get("container", {}).get("type") or "").lower()
                has_video = any(
                    t.get("type") == "video" for t in data.get("tracks", [])
                )
                if "matroska" not in container and container:
                    return Eligibility(False, "not_matroska", path)
                if not has_video:
                    return Eligibility(False, "no_video_track", path)
        return Eligibility(True, None, path)

    async def get_or_create_state(
        self, db: AsyncSession, movie_id: int
    ) -> LetterboxState:
        state = (
            await db.execute(
                select(LetterboxState).where(LetterboxState.movie_id == movie_id)
            )
        ).scalar_one_or_none()
        if state is None:
            state = LetterboxState(movie_id=movie_id, status="prefilter_candidate")
            db.add(state)
        return state

    async def apply(
        self,
        db: AsyncSession,
        movie: Movie,
        *,
        top: int,
        bottom: int,
        source: str = "api",
    ) -> ApplyResult:
        """Write symmetric/asymmetric pixel-crop tags to the movie's MKV."""
        if top < 0 or bottom < 0:
            raise IneligibleError("crop values must be non-negative")
        eligibility = await asyncio.to_thread(self.check_eligibility, movie)
        if not eligibility.eligible or eligibility.path is None:
            await self._log(db, movie.id, "error", source, {"reason": eligibility.reason})
            await db.commit()
            raise IneligibleError(eligibility.reason or "ineligible")

        path = eligibility.path
        async with self._file_lock(str(path)):
            result = await asyncio.to_thread(
                binaries.run,
                "mkvpropedit",
                [
                    binaries.safe_media_path(path), "--edit", "track:v1",
                    "--set", f"pixel-crop-top={top}",
                    "--set", f"pixel-crop-bottom={bottom}",
                    "--set", "pixel-crop-left=0",
                    "--set", "pixel-crop-right=0",
                ],
            )
            if not result.ok:
                await self._log(
                    db, movie.id, "error", source,
                    {"reason": "mkvpropedit_failed", "stderr": result.stderr.strip()[:300]},
                )
                await db.commit()
                raise IneligibleError(f"mkvpropedit failed: {result.stderr.strip()[:200]}")

            # Verify: re-read if the build supports it; else trust exit code 0.
            read_back = await asyncio.to_thread(read_applied_crop, path)
            verified = read_back is None or read_back == (top, bottom)

        state = await self.get_or_create_state(db, movie.id)
        state.status = "tagged"
        state.applied_crop_top = top
        state.applied_crop_bottom = bottom
        state.last_applied_at = datetime.now(UTC)
        state.error = None
        await self._log(
            db, movie.id, "apply", source,
            {"top": top, "bottom": bottom, "verified": verified, "path": str(path)},
        )
        await db.commit()
        logger.info(
            "LETTERBOX APPLIED | movie=%s | %d/%dpx | verified=%s", movie.title, top, bottom, verified
        )
        return ApplyResult(applied=True, top=top, bottom=bottom, path=str(path), verified=verified)

    async def remove(
        self, db: AsyncSession, movie: Movie, *, source: str = "api"
    ) -> RemoveResult:
        """Delete pixel-crop tags (idempotent)."""
        eligibility = await asyncio.to_thread(self.check_eligibility, movie)
        if not eligibility.eligible or eligibility.path is None:
            raise IneligibleError(eligibility.reason or "ineligible")
        path = eligibility.path
        async with self._file_lock(str(path)):
            await asyncio.to_thread(
                binaries.run,
                "mkvpropedit",
                [
                    binaries.safe_media_path(path), "--edit", "track:v1",
                    "--delete", "pixel-crop-top",
                    "--delete", "pixel-crop-bottom",
                    "--delete", "pixel-crop-left",
                    "--delete", "pixel-crop-right",
                ],
            )

        state = await self.get_or_create_state(db, movie.id)
        state.applied_crop_top = None
        state.applied_crop_bottom = None
        # Drop back to candidate when we still have a recommendation, else skipped.
        state.status = "candidate" if state.recommended_crop_top else "skipped"
        await self._log(db, movie.id, "remove", source, {"path": str(path)})
        await db.commit()
        logger.info("LETTERBOX REMOVED | movie=%s | path=%s", movie.title, path)
        return RemoveResult(removed=True, path=str(path))

    def _file_lock(self, path: str) -> asyncio.Lock:
        return _file_lock(path)

    async def _log(
        self, db: AsyncSession, movie_id: int, action: str, source: str, detail: dict
    ) -> None:
        db.add(
            LetterboxEvent(
                movie_id=movie_id,
                action=action,
                source=source,
                detail=json.dumps(detail, default=str),
            )
        )


letterbox_service = LetterboxService()
