"""allow one canonical poster job to project one run per subject

Stage-major poster groups share one canonical job and attempt while retaining
one independently reviewable ``PipelineRun`` per member. ``subject_key`` is a
stable non-null identity because nullable subject foreign keys are not a safe
uniqueness boundary in PostgreSQL.

Revision ID: 0019_pipeline_subject_key
Revises: 0018_season_series
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019_pipeline_subject_key"
down_revision: str | None = "0018_season_series"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pipeline_runs",
        sa.Column("subject_key", sa.String(length=200), nullable=True),
    )
    op.execute(
        """
        UPDATE pipeline_runs
        SET subject_key = CASE
            WHEN NULLIF(BTRIM(subject_snapshot ->> 'display_id'), '') IS NOT NULL
                THEN LEFT(BTRIM(subject_snapshot ->> 'display_id'), 200)
            WHEN media_type = 'movie' AND movie_id IS NOT NULL
                THEN 'movie:' || movie_id::text
            WHEN media_type = 'series' AND series_id IS NOT NULL
                THEN 'series:' || series_id::text
            WHEN media_type = 'season' AND season_id IS NOT NULL
                THEN 'season:' || season_id::text
            ELSE 'run:' || run_id
        END
        """
    )
    op.alter_column("pipeline_runs", "subject_key", nullable=False)
    op.drop_constraint("uq_pipeline_runs_job_id", "pipeline_runs", type_="unique")
    op.create_unique_constraint(
        "uq_pipeline_runs_job_subject",
        "pipeline_runs",
        ["job_id", "subject_key"],
    )


def downgrade() -> None:
    duplicate_job_id = op.get_bind().execute(
        sa.text(
            "SELECT job_id FROM pipeline_runs "
            "GROUP BY job_id HAVING count(*) > 1 LIMIT 1"
        )
    ).scalar()
    if duplicate_job_id is not None:
        raise RuntimeError(
            "cannot downgrade while poster group jobs have multiple PipelineRun projections"
        )
    op.drop_constraint("uq_pipeline_runs_job_subject", "pipeline_runs", type_="unique")
    op.create_unique_constraint("uq_pipeline_runs_job_id", "pipeline_runs", ["job_id"])
    op.drop_column("pipeline_runs", "subject_key")
