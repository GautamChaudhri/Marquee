"""Retained legacy registration for deferred Dolby Vision conversion only.

``dovi_analyze`` is deliberately absent: JMC4B dispatches it exclusively
through ``handlers_dovi`` and the JMC3 execution context.  The remaining
conversion registration stays fail-closed until its separate destructive
migration is authorized.
"""

from __future__ import annotations

from typing import Any

from marquee.core.jobs.handlers import register
from marquee.database import _get_session_factory
from marquee.models import Job, Movie


@register("dovi_convert")
async def dovi_convert(job: Job) -> dict[str, Any]:
    from marquee.core.dovi_conversion import (  # noqa: PLC0415
        DoviConversionError,
        execute_conversion,
    )
    from marquee.core.media_files import (  # noqa: PLC0415
        MediaFileNotFoundError,
        MediaFileUnavailableError,
        ensure_media_file_for_movie,
        resolve_media_file,
    )

    factory = _get_session_factory()
    movie_id = int(job.request["movie_id"])
    kind = job.request.get("kind")
    if kind not in {"p5_to_p81", "p7_strip_el"}:
        raise DoviConversionError("invalid_kind", "Unsupported Dolby Vision conversion kind.")

    async with factory() as db:
        current = await db.get(Job, job.id)
        if current is None:
            raise RuntimeError("job not found")
        movie = await db.get(Movie, movie_id)
        if movie is None:
            raise RuntimeError("movie not found")
        media_file = await ensure_media_file_for_movie(db, movie)
        if media_file is None:
            raise DoviConversionError("no_media_file", "No media file is available for this movie.")
        try:
            resolved = await resolve_media_file(db, media_file.id)
        except (MediaFileNotFoundError, MediaFileUnavailableError) as exc:
            raise DoviConversionError("file_unavailable", str(exc)) from exc
        result = await execute_conversion(
            db,
            current,
            resolved=resolved,
            media_file=media_file,
            kind=kind,  # type: ignore[arg-type]
        )
        result["movie_id"] = movie_id
        return result
