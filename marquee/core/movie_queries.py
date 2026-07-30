from sqlalchemy import exists, or_, select

from marquee.models import MediaFile, Movie, PipelineRun

# The run statuses that put a movie in front of a human for a decision.
REVIEW_QUEUE_STATUSES = frozenset({"completed", "flagged_manual"})


def movie_downloaded():
    """Movie is present and has a file on disk.

    The one availability filter for movies — the films list, the pipeline
    summary, and every run-eligibility check share it, so a movie can never be
    counted as runnable by one surface and skipped by another.
    """
    return Movie.is_present.is_(True) & or_(
        Movie.movie_file_path.is_not(None),
        exists(
            select(MediaFile.id).where(
                MediaFile.movie_id == Movie.id,
                MediaFile.is_active.is_(True),
                MediaFile.is_present.is_(True),
            )
        ),
    )


def movie_review_pending():
    """Movie has an undecided pipeline result awaiting a human decision.

    Correlated ``EXISTS`` rather than ``id IN (SELECT movie_id ...)`` on
    purpose: ``PipelineRun.movie_id`` is NULL for every series/season run, and
    ``NOT IN`` over a set containing NULL is never true, so the negated form
    silently matched nothing at all as soon as one TV run existed.
    """
    return exists(
        select(PipelineRun.run_id).where(
            PipelineRun.movie_id == Movie.id,
            PipelineRun.media_type == "movie",
            PipelineRun.feedback_event_id.is_(None),
            PipelineRun.status.in_(tuple(REVIEW_QUEUE_STATUSES)),
        )
    )
