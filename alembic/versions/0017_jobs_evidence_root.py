"""rename the job evidence root from jmc3 to jobs

Attempt workspaces, captured logs, and registered artifacts live under
``DATA_DIR/<root>/``. That root was named after the build phase that introduced
it; it is now named for what it holds.

``job_logs.storage_key`` and ``job_artifacts.storage_key`` are relative keys
under the data root, so every existing row still points at ``jmc3/...``. This
rewrites those prefixes. The bytes themselves are moved by
``marquee.db_migration.migrate_evidence_root``, which runs under the same
migration advisory lock immediately after Alembic.

Both halves are prefix-scoped and idempotent: rows already carrying ``jobs/``
are untouched, so re-running is a no-op.

Revision ID: 0017_jobs_root
Revises: 0016_poster_scope
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017_jobs_root"
down_revision: str | None = "0016_poster_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_ROOT = "jmc3/"
_CURRENT_ROOT = "jobs/"


def _rewrite_prefix(table: str, source: str, destination: str) -> None:
    op.execute(
        f"""
        UPDATE {table}
        SET storage_key = '{destination}' || substring(storage_key from {len(source) + 1})
        WHERE storage_key LIKE '{source}%'
        """
    )


def upgrade() -> None:
    for table in ("job_logs", "job_artifacts"):
        _rewrite_prefix(table, _LEGACY_ROOT, _CURRENT_ROOT)


def downgrade() -> None:
    for table in ("job_logs", "job_artifacts"):
        _rewrite_prefix(table, _CURRENT_ROOT, _LEGACY_ROOT)
