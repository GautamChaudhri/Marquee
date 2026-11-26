"""The system summary: what this installation is and how it is set, read-only.

Every field is named here; nothing serialises the settings object, so no key, token,
password, database URL, or environment variable can reach an answer by accident.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from marquee.api.schemas.jobs import JobState


class SchemaInfo(BaseModel):
    current: str | None = Field(description="The database's revision; null when unmigrated")
    head: str = Field(description="The newest revision in this code")
    at_head: bool


class SettingsInfo(BaseModel):
    deploy_policy: Literal["off", "missing", "all"]
    run_new_movies: bool
    sync_interval_minutes: int = Field(description="0: the scheduled sync is off")
    movie_roots: list[str] = Field(description="The movie folders, as this process sees them")
    data_dir: str


class LibraryInfo(BaseModel):
    movies: int = Field(description="Movies Radarr lists")
    movies_with_file: int
    posters: dict[str, int] = Field(description="Poster rows by origin")
    runs: dict[str, int] = Field(description="The product loop's runs by state (not masked)")
    masked_runs: dict[str, int] = Field(
        description="Blind evaluations' masked runs by state, counted apart"
    )
    deployments: int
    exemplars: dict[str, dict[str, int]] = Field(description="state -> polarity -> count")
    preference_events: int


class ModelFile(BaseModel):
    item: str = Field(description="The `marquee models fetch` item it belongs to")
    path: str = Field(description="Relative to the data directory")
    kind: Literal["file", "folder"]
    present: bool
    size_bytes: int | None = Field(description="On disk now; null when it is missing")
    expected_size_bytes: int = Field(description="What the manifest recorded")


class ModelsInfo(BaseModel):
    manifest: Literal["found", "missing", "unreadable"]
    files: list[ModelFile]


class LastSync(BaseModel):
    job_id: int
    state: JobState
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class MaintenanceJob(BaseModel):
    """The newest job of a maintenance kind."""

    job_id: int
    state: JobState
    created_at: datetime
    finished_at: datetime | None


class BackupsInfo(BaseModel):
    """The database dumps in the data folder's backups/db, and the scheduled dump."""

    interval_hours: int = Field(
        description="DB_BACKUP_INTERVAL_HOURS; 0: the scheduled dump is off"
    )
    keep: int = Field(description="DB_BACKUP_KEEP: scheduled dumps kept")
    count: int = Field(description="Dumps of every kind in the folder")
    total_bytes: int
    newest_at: datetime | None = Field(description="When the newest dump was made; null: none")
    newest_size_bytes: int | None
    stale: bool = Field(
        description="The schedule is on and the newest dump is missing or older than two intervals"
    )
    last_job: MaintenanceJob | None = Field(description="The newest db_backup job")


class RetentionInfo(BaseModel):
    """What retention deleted the last time it ran."""

    interval_hours: int = Field(description="RETENTION_INTERVAL_HOURS; 0: retention is off")
    days: int = Field(description="RETENTION_DAYS")
    last_job: MaintenanceJob | None = Field(description="The newest retention job")
    freed_items: int | None = Field(description="What the newest successful pass deleted")
    freed_bytes: int | None
    freed_at: datetime | None


class StorageInfo(BaseModel):
    """Bytes under the data directory, measured at most once a minute."""

    runs: int
    backups: int
    cache: int
    ml: int
    measured_at: datetime


class SystemInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    version: str
    environment: str
    schema_: SchemaInfo = Field(alias="schema")
    settings: SettingsInfo
    library: LibraryInfo
    models: ModelsInfo
    last_sync: LastSync | None = Field(description="The newest library sync job")
    backups: BackupsInfo
    retention: RetentionInfo
    storage: StorageInfo
