"""Deployment SQLAlchemy metadata for the canonical JMC2A schema.

JMC1 excluded the legacy job/media runtime tables here while their source
remained importable. JMC2A deleted those models, so deployment metadata now
equals the full ORM metadata; the guard remains so any future exclusion must
keep foreign keys consistent.
"""

from __future__ import annotations

from sqlalchemy import MetaData

from marquee.database import Base

EXCLUDED_DEPLOYMENT_TABLES: frozenset[str] = frozenset()


def get_deployment_metadata() -> MetaData:
    """Clone only tables owned by the Marquee deployment schema."""
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
