"""Deployment-only SQLAlchemy metadata for the JMC1 clean baseline.

The source tree temporarily retains legacy ORM classes so later migration
chunks can move handlers without import breakage.  Production Alembic must not
create those tables.  Tests may continue to use ``Base.metadata`` until the
obsolete source and its retained tests are removed in Chunk 5.
"""

from __future__ import annotations

from sqlalchemy import MetaData

from marquee.database import Base

EXCLUDED_DEPLOYMENT_TABLES = frozenset(
    {
        # Custom queue/resource/schedule/recovery authority.
        "job_resource_reservations",
        "job_resources",
        "job_schedules",
        "job_workers",
        # Duplicate media lifecycle authority.
        "media_batches",
        "media_job_events",
        "media_jobs",
        # These legacy evidence tables reference media_jobs and are rebuilt as
        # canonical job detail/evidence in later migration chunks.
        "letterbox_reencode_artifacts",
        "media_backups",
    }
)


def get_deployment_metadata() -> MetaData:
    """Clone only tables owned by the JMC1 Marquee deployment schema."""
    metadata = MetaData(naming_convention=Base.metadata.naming_convention)
    for table in Base.metadata.sorted_tables:
        if table.name in EXCLUDED_DEPLOYMENT_TABLES:
            continue
        excluded_targets = {
            foreign_key.column.table.name
            for foreign_key in table.foreign_keys
            if foreign_key.column.table.name in EXCLUDED_DEPLOYMENT_TABLES
        }
        if excluded_targets:
            names = ", ".join(sorted(excluded_targets))
            raise RuntimeError(f"deployment table {table.name!r} references excluded: {names}")
        table.to_metadata(metadata)
    return metadata
