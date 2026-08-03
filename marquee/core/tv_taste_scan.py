"""Discover deployed TV artwork on disk as taste-profile training evidence.

The movie taste profile is built from recorded preference events — what was shown,
what was picked. The TV profile is built differently: from the posters already
sitting in the library. Artwork that was deliberately put next to a show *is* the
statement of taste about that show, so the library is the training set.

Filenames come from ``SERIES_POSTER_FORMAT`` and ``SEASON_POSTER_FORMAT``, the same
settings the deploy path renders, so an operator who names artwork differently keeps
working without a code change. Season posters live in the series root next to the
show poster, not inside the season folders.

Scanning is filesystem work over network mounts — :func:`scan_tv_posters` keeps it
off the event loop.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.path_utils import PathValidationError, safe_translate_and_validate
from marquee.core.poster_files import sanitize_poster_filename
from marquee.core.runtime_settings import effective_settings as settings
from marquee.models import Season, Series

logger = logging.getLogger(__name__)

AssetKind = Literal["show", "season"]


@dataclass(frozen=True, slots=True)
class TvPosterRecord:
    """One deployed poster file and the subject it stands for."""

    path: Path
    asset_kind: AssetKind
    series_id: int
    series_title: str
    season_number: int | None

    @property
    def staged_name(self) -> str:
        """Collision-free filename for this record inside the training workspace."""
        if self.asset_kind == "season" and self.season_number is not None:
            return f"season-{self.series_id}-{self.season_number:02d}.jpg"
        return f"show-{self.series_id}.jpg"

    def identity(self) -> dict[str, Any]:
        """The tags carried into the profile so a poster stays attributable."""
        return {
            "asset_kind": self.asset_kind,
            "series_id": self.series_id,
            "series_title": self.series_title,
            "season_number": self.season_number,
        }


def _series_folder(series: Series) -> Path | None:
    """Resolve one Sonarr series path into a validated local folder."""
    if not series.series_path:
        return None
    try:
        folder = safe_translate_and_validate(series.series_path, source="sonarr")
    except (PathValidationError, ValueError):
        logger.warning(
            "Skipping series %s (%s): path did not validate: %s",
            series.id,
            series.title,
            series.series_path,
        )
        return None
    return folder if folder.is_dir() else None


def _render(fmt: str, *, season: int | None = None) -> str | None:
    """Render a configured poster filename, or None when the format is unusable."""
    try:
        rendered = fmt if season is None else fmt.format(season=season)
        return sanitize_poster_filename(rendered)
    except (KeyError, IndexError, ValueError, PathValidationError):
        logger.warning("Poster filename format is invalid: %r", fmt)
        return None


def _collect(
    series_rows: list[Series], seasons_by_series: dict[int, list[Season]]
) -> list[TvPosterRecord]:
    """Walk the resolved series folders. Blocking filesystem work."""
    records: list[TvPosterRecord] = []
    show_name = _render(settings.SERIES_POSTER_FORMAT)
    for series in series_rows:
        folder = _series_folder(series)
        if folder is None:
            continue
        if show_name is not None and (folder / show_name).is_file():
            records.append(
                TvPosterRecord(
                    path=folder / show_name,
                    asset_kind="show",
                    series_id=series.id,
                    series_title=series.title,
                    season_number=None,
                )
            )
        for season in seasons_by_series.get(series.id, ()):
            season_name = _render(settings.SEASON_POSTER_FORMAT, season=season.season_number)
            if season_name is None:
                continue
            poster = folder / season_name
            if not poster.is_file():
                continue
            records.append(
                TvPosterRecord(
                    path=poster,
                    asset_kind="season",
                    series_id=series.id,
                    series_title=series.title,
                    season_number=season.season_number,
                )
            )
    return records


async def scan_tv_posters(session: AsyncSession) -> list[TvPosterRecord]:
    """Find every deployed show and season poster in the present TV library.

    A poster counts as evidence whenever the file exists, whether or not the season
    has downloaded episodes — the artwork was still curated by hand. Series whose
    path fails validation are skipped with a warning rather than failing the scan.
    """
    series_rows = list(
        (
            await session.scalars(
                select(Series).where(Series.is_present.is_(True)).order_by(Series.id)
            )
        ).all()
    )
    if not series_rows:
        return []
    season_rows = list(
        (
            await session.scalars(
                select(Season)
                .where(
                    Season.is_present.is_(True),
                    Season.series_id.in_([series.id for series in series_rows]),
                )
                .order_by(Season.series_id, Season.season_number)
            )
        ).all()
    )
    seasons_by_series: dict[int, list[Season]] = {}
    for season in season_rows:
        seasons_by_series.setdefault(season.series_id, []).append(season)
    return await asyncio.to_thread(_collect, series_rows, seasons_by_series)
