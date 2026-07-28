"""backfill the parent series on season pipeline runs

``PosterPipelineRequestV1`` allows exactly one subject, so a season analysis
request carries ``season_id`` and no ``series_id``. ``PipelineRun`` copied the
request verbatim, which left every season run with a null ``series_id`` — and the
TV review queue groups runs by series, so those runs were silently invisible.

The projection now denormalizes the parent series. This backfills the rows
written before it did. Rows whose season has since been deleted keep their null.

Revision ID: 0018_season_series
Revises: 0017_jobs_root
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018_season_series"
down_revision: str | None = "0017_jobs_root"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE pipeline_runs AS run
        SET series_id = season.series_id
        FROM seasons AS season
        WHERE run.season_id = season.id
          AND run.series_id IS NULL
        """
    )


def downgrade() -> None:
    # The column is derived, not authored: a season run's series is always
    # recoverable from its season, so clearing it again loses nothing.
    op.execute(
        """
        UPDATE pipeline_runs
        SET series_id = NULL
        WHERE season_id IS NOT NULL
        """
    )
