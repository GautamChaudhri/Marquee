from sqlalchemy import exists, select

from marquee.models import Season, Series


def season_downloaded():
    """Season has at least one downloaded episode file."""
    return Season.episode_file_count > 0


def series_visible():
    """Series has at least one downloaded season (D3)."""
    return exists(
        select(Season.id).where(
            Season.series_id == Series.id, Season.episode_file_count > 0
        )
    )
