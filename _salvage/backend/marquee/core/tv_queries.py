from sqlalchemy import and_, exists, or_, select

from marquee.core.review_queries import terminal_review_conditions
from marquee.models import Job, PipelineRun, Season, Series


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


def series_review_pending():
    """Series has a show or season result visible in the TV Review tab."""

    return exists(
        select(PipelineRun.run_id)
        .outerjoin(Job, Job.id == PipelineRun.job_id)
        .outerjoin(Season, Season.id == PipelineRun.season_id)
        .where(
            or_(
                and_(PipelineRun.media_type == "series", PipelineRun.series_id == Series.id),
                and_(PipelineRun.media_type == "season", Season.series_id == Series.id),
            ),
            *terminal_review_conditions(),
        )
    )
