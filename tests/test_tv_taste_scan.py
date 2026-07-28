"""Discovery of deployed TV artwork used as taste-profile training evidence."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.core.tv_taste_scan import scan_tv_posters
from marquee.models import Season, Series


async def _seed_series(
    db: AsyncSession,
    root: Path,
    *,
    title: str,
    sonarr_id: int,
    show_poster: bool = True,
    seasons: list[dict] | None = None,
    is_present: bool = True,
) -> Series:
    """Create a series folder with artwork on disk and the rows that describe it."""
    root.mkdir(parents=True, exist_ok=True)
    series = Series(
        title=title,
        year=2011,
        series_path=str(root),
        sonarr_id=sonarr_id,
        tvdb_id=sonarr_id + 5000,
        season_count=len(seasons or []),
        is_present=is_present,
    )
    if show_poster:
        (root / "show.jpg").write_bytes(b"show-art")
    db.add(series)
    await db.flush()
    for spec in seasons or []:
        db.add(
            Season(
                series_id=series.id,
                season_number=spec["number"],
                episode_count=spec.get("episode_count", 0),
                episode_file_count=spec.get("episode_file_count", 0),
                is_present=spec.get("is_present", True),
            )
        )
        if spec.get("poster", True):
            (root / f"season{spec['number']:02d}.jpg").write_bytes(b"season-art")
    await db.commit()
    return series


@pytest.mark.asyncio
async def test_scan_tags_every_poster_with_its_subject(db, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(tmp_path)])
    series = await _seed_series(
        db,
        tmp_path / "Barry",
        title="Barry",
        sonarr_id=1,
        seasons=[{"number": 1}, {"number": 4}],
    )

    records = await scan_tv_posters(db)

    assert {(r.asset_kind, r.season_number) for r in records} == {
        ("show", None),
        ("season", 1),
        ("season", 4),
    }
    assert {r.series_title for r in records} == {"Barry"}
    assert {r.series_id for r in records} == {series.id}
    # Staged names must not collide: they key the identity map handed to the trainer.
    assert len({r.staged_name for r in records}) == len(records)
    assert {r.staged_name for r in records} == {
        f"show-{series.id}.jpg",
        f"season-{series.id}-01.jpg",
        f"season-{series.id}-04.jpg",
    }


@pytest.mark.asyncio
async def test_scan_skips_subjects_without_artwork_on_disk(db, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(tmp_path)])
    await _seed_series(
        db,
        tmp_path / "Punisher",
        title="Punisher",
        sonarr_id=2,
        show_poster=False,
        seasons=[{"number": 1, "poster": False}, {"number": 2, "poster": True}],
    )

    records = await scan_tv_posters(db)

    # A season row without a file on disk contributes nothing, and a series with no
    # show poster still contributes its seasons.
    assert [(r.asset_kind, r.season_number) for r in records] == [("season", 2)]


@pytest.mark.asyncio
async def test_scan_counts_seasons_without_downloaded_episodes(db, tmp_path, monkeypatch) -> None:
    """Curated artwork is evidence whether or not the episodes were kept."""
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(tmp_path)])
    await _seed_series(
        db,
        tmp_path / "Ted Lasso",
        title="Ted Lasso",
        sonarr_id=3,
        show_poster=False,
        seasons=[{"number": 3, "episode_file_count": 0, "episode_count": 12}],
    )

    records = await scan_tv_posters(db)

    assert [(r.asset_kind, r.season_number) for r in records] == [("season", 3)]


@pytest.mark.asyncio
async def test_scan_excludes_retired_series_and_seasons(db, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(tmp_path)])
    await _seed_series(
        db,
        tmp_path / "Gone",
        title="Gone",
        sonarr_id=4,
        seasons=[{"number": 1}],
        is_present=False,
    )
    await _seed_series(
        db,
        tmp_path / "Here",
        title="Here",
        sonarr_id=5,
        seasons=[{"number": 1, "is_present": False}, {"number": 2}],
    )

    records = await scan_tv_posters(db)

    assert {(r.series_title, r.asset_kind, r.season_number) for r in records} == {
        ("Here", "show", None),
        ("Here", "season", 2),
    }


@pytest.mark.asyncio
async def test_scan_follows_the_configured_filename_formats(db, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(tmp_path)])
    monkeypatch.setattr(settings, "SERIES_POSTER_FORMAT", "poster.jpg")
    monkeypatch.setattr(settings, "SEASON_POSTER_FORMAT", "Season {season}.jpg")
    root = tmp_path / "Legion"
    await _seed_series(
        db, root, title="Legion", sonarr_id=6, show_poster=False, seasons=[{"number": 2}]
    )
    # Rewrite the artwork under the operator's naming scheme.
    (root / "season02.jpg").unlink()
    (root / "poster.jpg").write_bytes(b"show-art")
    (root / "Season 2.jpg").write_bytes(b"season-art")

    records = await scan_tv_posters(db)

    assert {(r.asset_kind, r.path.name) for r in records} == {
        ("show", "poster.jpg"),
        ("season", "Season 2.jpg"),
    }


@pytest.mark.asyncio
async def test_scan_skips_a_series_whose_path_does_not_validate(db, tmp_path, monkeypatch) -> None:
    """One unreachable series must not fail the whole scan."""
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(tmp_path)])
    await _seed_series(db, tmp_path / "Good", title="Good", sonarr_id=7)
    outside = tmp_path.parent / "outside-the-root"
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "show.jpg").write_bytes(b"show-art")
    await _seed_series(db, outside, title="Outside", sonarr_id=8)

    records = await scan_tv_posters(db)

    assert {r.series_title for r in records} == {"Good"}


@pytest.mark.asyncio
async def test_scan_of_an_empty_library_is_empty(db, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "MEDIA_ROOTS", [str(tmp_path)])
    assert await scan_tv_posters(db) == []
