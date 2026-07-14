"""Versioned database configuration authority and code-owned key catalog."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel, ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import Settings, settings
from marquee.core.pipeline_config import PipelineSettings, pipeline_settings
from marquee.core.subtitles.config import SubtitleSettings, subtitle_settings
from marquee.models.configuration import ConfigurationCurrent, ConfigurationRevision

CONFIGURATION_CHANNEL = "marquee_configuration"
CONFIGURATION_SCHEMA_VERSION = 1

Owner = Literal["app", "pipeline", "subtitle"]
Sensitivity = Literal["public", "secret"]
ApplyMode = Literal["hot", "next_job", "restart"]


@dataclass(frozen=True, slots=True)
class ConfigurationKey:
    key: str
    owner: Owner
    validation_owner: type[BaseModel]
    scope: str
    sensitivity: Sensitivity
    apply_mode: ApplyMode
    database_owned: bool
    title: str
    description: str | None


@dataclass(frozen=True, slots=True)
class ConfigurationState:
    version: int
    values: dict[str, Any]
    checksum: str
    schema_version: int

    @property
    def etag(self) -> str:
        return f'"configuration-{self.version}-{self.checksum[:12]}"'


class ConfigurationError(ValueError):
    """Base class for configuration validation and ownership errors."""


class ConfigurationVersionConflictError(ConfigurationError):
    def __init__(self, current: ConfigurationState) -> None:
        super().__init__(f"configuration version is stale; current version is {current.version}")
        self.current = current


class ConfigurationUnavailableError(ConfigurationError):
    """Raised when the required current revision is absent or inconsistent."""


PIPELINE_RESTART_KEYS = frozenset(
    {
        "AI_MODEL",
        "EXECUTION_PROVIDER",
        "CLIP_MODEL_PATH",
        "AESTHETIC_MODEL_PATH",
        "FACE_MODEL_PATH",
        "TASTE_PROFILE_PATH",
        "DINO_MODEL_PATH",
        "PERSON_MODEL_PATH",
        "ZEROSHOT_AXES_PATH",
        "LEARNED_HEAD_PATH",
        "EMBEDDING_CACHE_DIR",
        "FEEDBACK_LABELS_PATH",
        "NEGATIVE_DATA_DIR",
        "TRAINING_DATA_DIR",
    }
) | frozenset(
    key
    for key, field in PipelineSettings.model_fields.items()
    if field.annotation is Path or Path in get_args(field.annotation)
)

APP_DATABASE_KEYS = frozenset(
    {
        "MOVIE_POSTER_FORMAT",
        "SERIES_POSTER_FORMAT",
        "SEASON_POSTER_FORMAT",
        "POSTER_RESTORE_METHOD",
        "HEAL_ENABLED",
        "HEAL_INTERVAL_MINUTES",
    }
)
APP_RESTART_KEYS = frozenset({"POSTER_BACKUP_DIR"})

SUBTITLE_DATABASE_KEYS = frozenset(
    {
        "SUBTITLE_ENABLED",
        "SUBTITLE_SCAN_CONCURRENCY",
        "SUBTITLE_MUTATION_CONCURRENCY",
        "SUBTITLE_GENERATION_CONCURRENCY",
        "SUBTITLE_PREFERRED_LANGUAGES",
        "SUBTITLE_PREFERRED_AUDIO_LANGUAGES",
        "SUBTITLE_PREFERRED_SUBTITLE_LANGUAGES",
        "SUBTITLE_UNKNOWN_LANGUAGE_ACTION",
        "SUBTITLE_PROTECT_FORCED",
        "SUBTITLE_PROTECT_LAST_FULL_DIALOGUE",
        "SUBTITLE_BACKUP_MODE",
        "SUBTITLE_EXTERNAL_DELETE_MODE",
        "AUDIO_SUBS_DEEP_SCAN_ENABLED",
        "AUDIO_SUBS_DEEP_SCAN_HOUR",
        "AUDIO_SUBS_DEEP_SCAN_BATCH",
        "SUBGEN_DEPLOYMENT",
        "SUBGEN_URL",
        "SUBGEN_PROFILE_NAME",
        "SUBGEN_MODEL_LABEL",
        "SUBGEN_MODE",
        "SUBGEN_LOCAL_PATH_PREFIX",
        "SUBGEN_REMOTE_PATH_PREFIX",
        "SUBGEN_NAMING_TYPE",
        "SUBGEN_NAME_INCLUDES_SUBGEN",
        "SUBGEN_NAME_INCLUDES_MODEL",
    }
)
SUBTITLE_SECRET_KEYS = frozenset({"SUBGEN_CALLBACK_TOKEN"})

_SECRET_LIKE = re.compile(r"(?:secret|token|password|api[_-]?key)", re.IGNORECASE)


def _title(key: str) -> str:
    return key.replace("_", " ").title()


def _catalog() -> dict[str, ConfigurationKey]:
    entries: dict[str, ConfigurationKey] = {}

    def add_owner(
        owner: Owner,
        model: type[BaseModel],
        *,
        database_keys: frozenset[str],
        restart_keys: frozenset[str] = frozenset(),
        secret_keys: frozenset[str] = frozenset(),
    ) -> None:
        for key, field in model.model_fields.items():
            if key not in database_keys | restart_keys | secret_keys:
                continue
            database_owned = key in database_keys
            entries[key] = ConfigurationKey(
                key=key,
                owner=owner,
                validation_owner=model,
                scope="execution" if owner != "app" else "application",
                sensitivity="secret" if key in secret_keys else "public",
                apply_mode="next_job" if database_owned else "restart",
                database_owned=database_owned,
                title=_title(key),
                description=field.description,
            )

    add_owner(
        "app",
        Settings,
        database_keys=APP_DATABASE_KEYS,
        restart_keys=APP_RESTART_KEYS,
    )
    add_owner(
        "pipeline",
        PipelineSettings,
        database_keys=frozenset(PipelineSettings.model_fields) - PIPELINE_RESTART_KEYS,
        restart_keys=PIPELINE_RESTART_KEYS,
    )
    add_owner(
        "subtitle",
        SubtitleSettings,
        database_keys=SUBTITLE_DATABASE_KEYS,
        restart_keys=(
            frozenset(SubtitleSettings.model_fields)
            - SUBTITLE_DATABASE_KEYS
            - SUBTITLE_SECRET_KEYS
        ),
        secret_keys=SUBTITLE_SECRET_KEYS,
    )
    return entries


CONFIGURATION_CATALOG = _catalog()

_OWNER_BASES: dict[Owner, BaseModel] = {
    "app": settings,
    "pipeline": pipeline_settings,
    "subtitle": subtitle_settings,
}


def canonical_json(values: dict[str, Any]) -> str:
    return json.dumps(values, sort_keys=True, separators=(",", ":"), allow_nan=False)


def configuration_checksum(values: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(values).encode()).hexdigest()


def _json_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def validate_database_values(values: dict[str, Any]) -> dict[str, Any]:
    """Validate a complete stored document and return normalized JSON values."""
    if not isinstance(values, dict):
        raise ConfigurationError("configuration values must be an object")

    for key in values:
        entry = CONFIGURATION_CATALOG.get(key)
        if entry is None:
            kind = "secret-like" if _SECRET_LIKE.search(key) else "unknown"
            raise ConfigurationError(f"{kind} configuration key is not allowed: {key}")
        if entry.sensitivity == "secret":
            raise ConfigurationError(f"secret configuration key is environment-owned: {key}")
        if not entry.database_owned:
            raise ConfigurationError(
                f"configuration key is {entry.apply_mode}-owned and cannot be stored: {key}"
            )

    normalized: dict[str, Any] = {}
    for owner, base in _OWNER_BASES.items():
        owner_updates = {
            key: value
            for key, value in values.items()
            if CONFIGURATION_CATALOG[key].owner == owner
        }
        if not owner_updates:
            continue
        model_type = type(base)
        merged = {**base.model_dump(), **owner_updates}
        try:
            validated = model_type(**merged)
        except ValidationError as exc:
            raise ConfigurationError(f"invalid {owner} configuration: {exc}") from exc
        for key in owner_updates:
            normalized[key] = _json_value(getattr(validated, key))

    canonical_json(normalized)
    return dict(sorted(normalized.items()))


async def read_current_configuration(
    session: AsyncSession,
    *,
    for_update: bool = False,
) -> ConfigurationState:
    statement = select(ConfigurationCurrent).where(ConfigurationCurrent.singleton_id == 1)
    if for_update:
        statement = statement.with_for_update()
    pointer = await session.scalar(statement)
    if pointer is None:
        raise ConfigurationUnavailableError("configuration_current singleton is missing")
    revision = await session.get(ConfigurationRevision, pointer.current_version)
    if revision is None:
        raise ConfigurationUnavailableError(
            f"configuration revision {pointer.current_version} is missing"
        )
    normalized = validate_database_values(revision.values)
    checksum = configuration_checksum(normalized)
    if revision.schema_version != CONFIGURATION_SCHEMA_VERSION or revision.checksum != checksum:
        raise ConfigurationUnavailableError(
            f"configuration revision {revision.version} failed schema/checksum validation"
        )
    return ConfigurationState(
        version=revision.version,
        values=normalized,
        checksum=checksum,
        schema_version=revision.schema_version,
    )


async def update_configuration(
    session: AsyncSession,
    *,
    expected_version: int,
    updates: dict[str, Any],
    actor: dict[str, Any],
    trigger: str,
) -> tuple[ConfigurationState, bool]:
    """Atomically validate, append, point, and notify one optimistic update."""
    current = await read_current_configuration(session, for_update=True)
    if expected_version != current.version:
        raise ConfigurationVersionConflictError(current)

    merged = {**current.values, **updates}
    normalized = validate_database_values(merged)
    if normalized == current.values:
        return current, False

    version = current.version + 1
    checksum = configuration_checksum(normalized)
    revision = ConfigurationRevision(
        version=version,
        values=normalized,
        checksum=checksum,
        schema_version=CONFIGURATION_SCHEMA_VERSION,
        actor=actor,
        trigger=trigger,
    )
    session.add(revision)
    await session.flush()
    pointer = await session.get(ConfigurationCurrent, 1)
    if pointer is None:
        raise ConfigurationUnavailableError("configuration_current singleton disappeared")
    pointer.current_version = version
    await session.execute(
        text("SELECT pg_notify(:channel, :payload)"),
        {"channel": CONFIGURATION_CHANNEL, "payload": str(version)},
    )
    await session.flush()
    return (
        ConfigurationState(
            version=version,
            values=normalized,
            checksum=checksum,
            schema_version=CONFIGURATION_SCHEMA_VERSION,
        ),
        True,
    )


def effective_owner_values(state: ConfigurationState, owner: Owner) -> dict[str, Any]:
    base = _OWNER_BASES[owner]
    values = base.model_dump()
    values.update(
        {
            key: value
            for key, value in state.values.items()
            if CONFIGURATION_CATALOG[key].owner == owner
        }
    )
    return values
