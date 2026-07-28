"""Canonical, non-destructive poster projection reconciliation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import or_, select

from marquee.core.jobs.artifact_service import register_physical_artifact
from marquee.core.jobs.delivery import ExecutionContext, register_execution_handler
from marquee.core.jobs.documents import PosterRescanRequestV1, PosterRescanResultV1
from marquee.core.jobs.progress import (
    MeasurementMode,
    ProgressMeasurementUpdate,
)
from marquee.core.jobs.progress_service import ProgressObservation, progress_writer
from marquee.core.path_utils import safe_translate_and_validate
from marquee.core.poster_subjects import PosterSubject
from marquee.core.tv_queries import season_downloaded, series_visible
from marquee.models import MediaFile, Movie, Season, Series

_MAX_HASH_BYTES = 32 * 1024 * 1024
_MAX_EVIDENCE_ROWS = 500


@dataclass(frozen=True, slots=True)
class _Observation:
    media_type: str
    subject_id: int
    expected_path: str
    exists: bool
    checksum: str | None


def _downloaded_movie():
    return or_(
        Movie.movie_file_path.is_not(None),
        select(MediaFile.id)
        .where(MediaFile.movie_id == Movie.id, MediaFile.is_active.is_(True))
        .exists(),
    )


def _checksum(path: Path) -> str | None:
    if not path.is_file() or path.stat().st_size > _MAX_HASH_BYTES:
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


async def _emit_progress(context: ExecutionContext, completed: int, total: int) -> None:
    await progress_writer.safe_write(
        job_id=context.delivery.canonical_job_id,
        attempt_id=context.attempt.attempt_id,
        fence_token=context.attempt.fence_token,
        observation=ProgressObservation(
            stage_key="reconciling",
            overall=ProgressMeasurementUpdate(
                scope_id="poster-rescan:subjects",
                mode=MeasurementMode.DETERMINATE,
                unit="subjects",
                completed=completed,
                total=total,
            ),
            current=ProgressMeasurementUpdate(
                scope_id="poster-rescan:current",
                mode=MeasurementMode.DETERMINATE,
                unit="subjects",
                completed=completed,
                total=total,
            ),
            producer_ordinal=max(1, completed),
        ),
    )


async def _subjects(
    context: ExecutionContext, request: PosterRescanRequestV1
) -> list[PosterSubject]:
    async with context.session_factory() as session:
        subjects: list[PosterSubject] = []
        if request.scope in {"all", "movie"}:
            query = select(Movie).where(_downloaded_movie()).order_by(Movie.id)
            if request.movie_id is not None:
                query = query.where(Movie.id == request.movie_id)
            for movie in (await session.execute(query)).scalars():
                subjects.append(PosterSubject.from_movie(movie))
        if request.scope in {"all", "series"}:
            query = select(Series).where(series_visible()).order_by(Series.id)
            if request.series_id is not None:
                query = query.where(Series.id == request.series_id)
            series_rows = list((await session.execute(query)).scalars())
            for series in series_rows:
                subjects.append(PosterSubject.from_series(series))
            series_ids = [series.id for series in series_rows]
            if series_ids:
                season_rows = (
                    await session.execute(
                        select(Season, Series)
                        .join(Series, Series.id == Season.series_id)
                        .where(Season.series_id.in_(series_ids), season_downloaded())
                        .order_by(Season.id)
                    )
                ).all()
                for season, series in season_rows:
                    subjects.append(PosterSubject.from_season(season, series))
        return subjects


async def execute_poster_rescan(context: ExecutionContext) -> dict[str, object]:
    request = PosterRescanRequestV1.model_validate(context.request)
    subjects = await _subjects(context, request)
    observations: list[_Observation] = []
    warnings: list[str] = []
    for index, subject in enumerate(subjects, 1):
        if context.cancellation.cancel_called:
            raise asyncio.CancelledError
        try:
            folder = safe_translate_and_validate(subject.folder_raw, source=subject.path_source)
            expected = folder / subject.render_filename()
            exists = await asyncio.to_thread(expected.is_file)
            checksum = await asyncio.to_thread(_checksum, expected) if exists else None
            observations.append(
                _Observation(
                    media_type=subject.media_type,
                    subject_id=subject.entity.id,
                    expected_path=str(expected),
                    exists=exists,
                    checksum=checksum,
                )
            )
        except Exception:
            warnings.append(f"{subject.media_type}:{subject.entity.id}:unavailable")
        if index == 1 or index % 25 == 0 or index == len(subjects):
            await _emit_progress(context, index, len(subjects))

    if len(observations) > _MAX_EVIDENCE_ROWS:
        warnings.append("evidence:truncated")
    report = {
        "version": 1,
        "scope": request.scope,
        "observed": len(observations),
        "observations": [asdict(observation) for observation in observations[:_MAX_EVIDENCE_ROWS]],
        "warnings": warnings[:20],
    }
    encoded = json.dumps(report, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()
    staged, fd = context.workspace.staging_file("poster-rescan.json")
    try:
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)
    artifact = await register_physical_artifact(
        job_id=context.delivery.canonical_job_id,
        attempt_id=context.attempt.attempt_id,
        fence_token=context.attempt.fence_token,
        source=staged,
        kind="command_report",
        name="Poster rescan observations",
        content_type="application/json",
        retention_class="extended",
        metadata={"scope": request.scope, "observed": len(observations)},
    )

    changed = missing = 0
    async with context.session_factory() as session, session.begin():
        if context.cancellation.cancel_called:
            raise asyncio.CancelledError
        if not await context.writer.owns_current_attempt(session):
            raise RuntimeError("stale poster rescan cannot update derived projections")
        for observation in observations:
            model = {"movie": Movie, "series": Series, "season": Season}[observation.media_type]
            entity = await session.get(model, observation.subject_id, with_for_update=True)
            if entity is None:
                warnings.append(f"{observation.media_type}:{observation.subject_id}:retired")
                continue
            next_path = observation.expected_path if observation.exists else None
            next_checksum = observation.checksum if observation.exists else None
            if entity.poster_path != next_path or entity.poster_sha256 != next_checksum:
                entity.poster_path = next_path
                entity.poster_sha256 = next_checksum
                changed += 1
            if not observation.exists:
                missing += 1
    return PosterRescanResultV1(
        outcome="succeeded" if changed else "no_change",
        observed=len(observations),
        changed=changed,
        missing=missing,
        artifact_ids=(artifact.id,),
        warnings=tuple(warnings[:20]),
    ).model_dump(mode="json")


register_execution_handler("poster_rescan", execute_poster_rescan)
