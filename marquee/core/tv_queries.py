from sqlalchemy import exists, select

from marquee.models import Season, Series


def season_downloaded():
    """Season is present and has at least one downloaded episode file."""
    return Season.is_present.is_(True) & (Season.episode_file_count > 0)


def series_visible():
    """Series is present and has at least one present downloaded season (D3)."""
    return Series.is_present.is_(True) & exists(
        select(Season.id).where(
            Season.series_id == Series.id,
            Season.is_present.is_(True),
            Season.episode_file_count > 0,
        )
    )
