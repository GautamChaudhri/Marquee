"""Versioned database configuration authority and code-owned key catalog."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel, ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import Settings, settings
from marquee.core.pipeline_config import PipelineSettings, pipeline_settings
from marquee.core.pipeline_config_meta import KNOB_GROUPS, KNOB_META
from marquee.models.configuration import ConfigurationCurrent, ConfigurationRevision

CONFIGURATION_CHANNEL = "marquee_configuration"
CONFIGURATION_SCHEMA_VERSION = 1

Owner = Literal["app", "pipeline"]
Storage = Literal["revision", "secret_store", "deployment", "internal"]
Sensitivity = Literal["public", "private", "secret"]
ApplyMode = Literal["hot", "next_job", "restart", "deployment"]
SettingsTab = Literal[
    "general", "connections", "media", "posters", "pipeline", "taste", "system", "access"
]
SettingsLevel = Literal["standard", "advanced"]


@dataclass(frozen=True, slots=True)
class ConfigurationKey:
    key: str
    owner: Owner
    validation_owner: type[BaseModel]
    scope: str
    storage: Storage
    sensitivity: Sensitivity
    apply_mode: ApplyMode
    title: str
    description: str | None
    tab: SettingsTab
    section: str
    level: SettingsLevel
    control: dict[str, Any]
    visible: bool = True

    @property
    def database_owned(self) -> bool:
        """Compatibility view for execution snapshots and legacy clients."""
        return self.storage == "revision"

    @property
    def editable(self) -> bool:
        return self.storage in {"revision", "secret_store"}


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
        "EMBEDDING_CACHE_DIR",
    }
) | frozenset(
    key
    for key, field in PipelineSettings.model_fields.items()
    if field.annotation is Path or Path in get_args(field.annotation)
)

PIPELINE_INTERNAL_KEYS = frozenset(
    {
        "OCR_TEXT_MODE",
        "OCR_ALLOW_TITLE",
        "OCR_ALLOW_DIRECTOR",
        "OCR_ALLOW_STUDIO",
        "OCR_ALLOW_RATING",
        "OCR_ALLOW_TAGLINE",
        "OCR_ALLOW_BILLING",
        "OCR_ALLOW_SEASON",
    }
)

APP_SECRET_STORE_KEYS = frozenset({"TMDB_READ_ACCESS_TOKEN", "RADARR_API_KEY", "SONARR_API_KEY"})
APP_SECRET_KEYS = APP_SECRET_STORE_KEYS | frozenset({"API_KEY"})
APP_PRIVATE_KEYS = frozenset(
    {
        "DB_URL",
        "DATA_PATH_CEILING",
        "DATA_DIR",
        "METRICS_DISK_PATH",
        "BACKUP_DIR",
        "RADARR_PATH_PREFIX",
        "RADARR_MEDIA_PATH",
        "SONARR_PATH_PREFIX",
        "SONARR_MEDIA_PATH",
        "MEDIA_ROOTS",
        "MEDIA_PATH_CEILINGS",
        "POSTER_CACHE_DIR",
        "POSTER_STAGING_DIR",
        "POSTER_BACKUP_DIR",
        "MARQUEE_SETTINGS_KEYRING_FILE",
        "API_KEY_FILE",
        "POSTGRES_PASSWORD_FILE",
    }
)
APP_DEPLOYMENT_KEYS = frozenset(
    {
        "MARQUEE_ENVIRONMENT",
        "MARQUEE_PROCESS_ROLE",
        "HOST",
        "PORT",
        "DEBUG",
        "API_KEY",
        "API_KEY_FILE",
        "DB_URL",
        "DATA_PATH_CEILING",
        "POSTGRES_PASSWORD_FILE",
        "DB_LOCK_TIMEOUT_MS",
        "DB_IDLE_TXN_TIMEOUT_MS",
        "DB_API_POOL_SIZE",
        "DB_API_MAX_OVERFLOW",
        "DB_WORKER_POOL_SIZE",
        "DB_WORKER_MAX_OVERFLOW",
        "DB_SCHEDULER_POOL_SIZE",
        "DB_SCHEDULER_MAX_OVERFLOW",
        "DB_MIGRATION_CONNECTIONS",
        "DB_DEPLOYMENT_MAX_CONNECTIONS",
        "JOB_EMBEDDED_WORKERS",
        "JOB_EMBEDDED_WORKER_COUNT",
        "JOB_WORKER_NODE_ID",
        "JOB_RUNNER_UID",
        "JOB_RUNNER_GID",
        "JOB_WORKER_ENTRYPOINTS",
        "MARQUEE_SETTINGS_KEYRING_FILE",
        "MEDIA_PATH_CEILINGS",
    }
)
APP_REVISION_KEYS = frozenset(Settings.model_fields) - APP_DEPLOYMENT_KEYS - APP_SECRET_STORE_KEYS
# Compatibility alias for older callers and design documents.
APP_DATABASE_KEYS = APP_REVISION_KEYS

APP_NEXT_JOB_KEYS = frozenset(
    {
        "AUTH_ALLOW_LOCAL",
        "AUTH_BRUTE_LOCKOUT_ATTEMPTS",
        "AUTH_BRUTE_WINDOW_SECONDS",
        "AUTH_BRUTE_LOCKOUT_SECONDS",
        "RADARR_URL",
        "SONARR_URL",
        "RADARR_PATH_PREFIX",
        "RADARR_MEDIA_PATH",
        "SONARR_PATH_PREFIX",
        "SONARR_MEDIA_PATH",
        "MEDIA_ROOTS",
        "SYNC_INTERVAL_MINUTES",
        "SYNC_COOLDOWN_SECONDS",
        "HEAL_INTERVAL_MINUTES",
        "HEAL_ENABLED",
        "HEAL_RECENT_DEPLOY_GRACE_MINUTES",
        "RATE_PIPELINE_RUN_SECONDS",
        "RATE_TASTE_RETRAIN_SECONDS",
        "RATE_TASTE_MAP_REBUILD_SECONDS",
        "RATE_TASTE_ENRICH_SECONDS",
        "WEBHOOK_DRY_RUN",
        "MOVIE_POSTER_FORMAT",
        "SERIES_POSTER_FORMAT",
        "SEASON_POSTER_FORMAT",
        "POSTER_RESTORE_METHOD",
        "JOB_PRODUCTION_SCHEDULES_ENABLED",
        "JOB_RETENTION_DAYS",
    }
)
APP_RESTART_KEYS = APP_REVISION_KEYS - APP_NEXT_JOB_KEYS

PIPELINE_TASTE_KEYS = frozenset(
    {
        "K_NEIGHBORS",
        "KNN_WEIGHTING",
        "KNN_SOFTMAX_TEMP",
        "TASTE_NEG_WEIGHT",
        "TV_TASTE_MIN_POSTERS",
        "TASTE_SEEDING_DIR",
        "TASTE_SEEDING_MIN_POSTERS",
        "TASTE_MAP_MIN_CLUSTER_SIZE_RATIO",
        "TASTE_MAP_CLUSTER_EPSILON",
        "TASTE_MAP_CLUSTER_METHOD",
        "TASTE_PROFILE_PATH",
        "TASTE_PROFILE_TV_PATH",
        "ZEROSHOT_AXES_PATH",
        "CALIBRATION_ENABLED",
        "CALIBRATION_BANDWIDTH_SCALE",
        "CALIBRATION_MIN_SAMPLES",
        "SCORER",
        "FEEDBACK_GATE_ALERT_THRESHOLD",
        "FEEDBACK_DEPLOY_DEFAULT",
        "FEEDBACK_HARD_NEGATIVE_RANK_MAX",
        "RESIDUAL_MIN_SUBJECTS",
        "RESIDUAL_MIN_PAIRS",
        "TYPICALITY_FEATURES",
        "WEIGHT_KNN_SIM",
        "WEIGHT_DINO_KNN",
        "WEIGHT_TASTE_TYPICALITY",
    }
)
PIPELINE_STANDARD_KEYS = frozenset(
    {
        "AI_MODEL",
        "EXECUTION_PROVIDER",
        "PREFERRED_LANG",
        "DINO_ENABLED",
        "EXTRA_QUALITY_ENABLED",
        "TMDB_POSTER_SIZE",
        "PIPELINE_BATCH_MAX_MOVIES",
        "POSTER_GROUP_ENABLED",
        "POSTER_GROUP_BATCH_MODE",
        "POSTER_GROUP_CHUNK_SIZE",
    }
)


_SECRET_LIKE = re.compile(r"(?:secret|token|password|api[_-]?key)", re.IGNORECASE)


def _title(key: str) -> str:
    return key.replace("_", " ").title()


def _control_for(model: type[BaseModel], key: str) -> dict[str, Any]:
    field = model.model_fields[key]
    control = dict(KNOB_META.get(key, {}))
    if not control:
        annotation = field.annotation
        origin = get_origin(annotation)
        args = tuple(arg for arg in get_args(annotation) if arg is not type(None))
        candidate = args[0] if len(args) == 1 else annotation
        if origin is Literal:
            control = {"kind": "enum", "options": list(get_args(annotation))}
        elif origin in {list, tuple, set}:
            control = {"kind": "list"}
        elif candidate is bool:
            control = {"kind": "bool"}
        elif candidate is int:
            control = {"kind": "int", "step": 1}
        elif candidate is float:
            control = {"kind": "float", "step": 0.1}
        elif candidate is Path or Path in args:
            control = {"kind": "path"}
        else:
            control = {"kind": "str"}

    for constraint in field.metadata:
        for source, target in (("ge", "min"), ("gt", "min"), ("le", "max"), ("lt", "max")):
            value = getattr(constraint, source, None)
            if value is not None and target not in control:
                control[target] = value
    if field.description:
        control["help"] = field.description
    return control


def _app_ui(key: str) -> tuple[SettingsTab, str, SettingsLevel]:
    if key in {
        "APP_NAME",
        "MARQUEE_ENVIRONMENT",
        "MARQUEE_PROCESS_ROLE",
        "HOST",
        "PORT",
    }:
        return ("general", "Application", "standard")
    if key in {
        "TMDB_READ_ACCESS_TOKEN",
        "RADARR_URL",
        "RADARR_API_KEY",
        "SONARR_URL",
        "SONARR_API_KEY",
        "SYNC_INTERVAL_MINUTES",
    }:
        return ("connections", "Services", "standard")
    if key in {"SYNC_COOLDOWN_SECONDS", "WEBHOOK_DRY_RUN"}:
        return ("connections", "Synchronization", "advanced")
    if key in {
        "MEDIA_ROOTS",
        "RADARR_PATH_PREFIX",
        "RADARR_MEDIA_PATH",
        "SONARR_PATH_PREFIX",
        "SONARR_MEDIA_PATH",
    }:
        return ("media", "Library paths", "standard")
    if key in {
        "DATA_DIR",
        "DATA_PATH_CEILING",
        "MEDIA_PATH_CEILINGS",
        "METRICS_DISK_PATH",
        "POSTER_CACHE_DIR",
        "POSTER_STAGING_DIR",
    }:
        return ("media", "Application paths", "advanced")
    if key in {
        "MOVIE_POSTER_FORMAT",
        "SERIES_POSTER_FORMAT",
        "SEASON_POSTER_FORMAT",
        "POSTER_RESTORE_METHOD",
        "HEAL_ENABLED",
        "HEAL_INTERVAL_MINUTES",
    }:
        return ("posters", "Poster behavior", "standard")
    if key in {
        "HEAL_RECENT_DEPLOY_GRACE_MINUTES",
        "POSTER_BACKUP_DIR",
    }:
        return ("posters", "Storage and healing", "advanced")
    if key in {"PIPELINE_CACHE_EXTRACTOR", "RATE_PIPELINE_RUN_SECONDS"}:
        return ("pipeline", "Runtime defaults", "standard")
    if key == "ONBOARDING_ENABLED":
        return ("taste", "Onboarding", "standard")
    if key.startswith("RATE_TASTE_"):
        return ("taste", "Rate limits", "advanced")
    if key in {"API_KEY", "AUTH_ALLOW_LOCAL", "CORS_ORIGINS", "DEBUG"}:
        return ("access", "Request access", "standard")
    if key.startswith("AUTH_BRUTE_") or key in {
        "MAX_REQUEST_BODY_BYTES",
        "MARQUEE_SETTINGS_KEYRING_FILE",
        "API_KEY_FILE",
    }:
        return ("access", "Defensive limits", "advanced")
    if key == "POSTGRES_PASSWORD_FILE":
        return ("system", "Database", "advanced")
    if key in {
        "LOG_LEVEL",
        "LOG_FORMAT",
        "BACKUP_INTERVAL_HOURS",
        "BACKUP_RETENTION_DAYS",
        "BACKUP_INITIAL_DELAY_SECONDS",
        "BACKUP_DIR",
        "METRICS_SAMPLE_INTERVAL_SECONDS",
        "METRICS_RETENTION_DAYS",
        "JOB_RETENTION_DAYS",
        "JOB_PRODUCTION_SCHEDULES_ENABLED",
    }:
        return ("system", "Operations", "standard")
    if key.startswith("DB_"):
        return ("system", "Database", "advanced")
    if key.startswith("HEALTH_"):
        return ("system", "Health checks", "advanced")
    if key.startswith("JOB_"):
        if any(part in key for part in ("LOG_", "EVENT_", "ARTIFACT_")):
            return ("system", "Evidence and streams", "advanced")
        if any(part in key for part in ("CONCURRENCY", "SLOTS", "ENTRYPOINTS")):
            return ("system", "Worker resources", "advanced")
        return ("system", "Job runtime", "advanced")
    return ("system", "Runtime", "advanced")


_PIPELINE_GROUP_BY_KEY = {
    key: str(group["label"]) for group in KNOB_GROUPS for key in group["knobs"]
}


def _pipeline_ui(key: str) -> tuple[SettingsTab, str, SettingsLevel]:
    if key in PIPELINE_INTERNAL_KEYS:
        return ("pipeline", "Text profile compatibility", "advanced")
    if key in PIPELINE_TASTE_KEYS:
        if key.startswith("TASTE_MAP_"):
            section = "Taste map"
        elif key.startswith(("FEEDBACK_", "RESIDUAL_", "SCORER")):
            section = "Residual feedback"
        elif key.endswith("_PATH") or key.endswith("_DIR"):
            section = "Taste artifacts"
        else:
            section = "Profile behavior"
        level: SettingsLevel = (
            "standard"
            if key
            in {
                "K_NEIGHBORS",
                "KNN_WEIGHTING",
                "KNN_SOFTMAX_TEMP",
                "TASTE_NEG_WEIGHT",
                "TV_TASTE_MIN_POSTERS",
                "SCORER",
                "RESIDUAL_MIN_SUBJECTS",
                "RESIDUAL_MIN_PAIRS",
                "FEEDBACK_GATE_ALERT_THRESHOLD",
                "FEEDBACK_DEPLOY_DEFAULT",
            }
            else "advanced"
        )
        return ("taste", section, level)
    if key in PIPELINE_STANDARD_KEYS:
        return ("pipeline", "Run defaults", "standard")
    if key.endswith("_PATH") or key.endswith("_DIR") or key in {"AI_MODEL", "EXECUTION_PROVIDER"}:
        return ("pipeline", "Models and artifacts", "advanced")
    if key.startswith("OCR_"):
        return ("pipeline", "OCR tuning", "advanced")
    return ("pipeline", _PIPELINE_GROUP_BY_KEY.get(key, "Scoring"), "advanced")


def _catalog() -> dict[str, ConfigurationKey]:
    entries: dict[str, ConfigurationKey] = {}

    models: tuple[tuple[Owner, type[BaseModel]], ...] = (
        ("app", Settings),
        ("pipeline", PipelineSettings),
    )
    for owner, model in models:
        for key, field in model.model_fields.items():
            if owner == "app":
                if key in APP_SECRET_STORE_KEYS:
                    storage: Storage = "secret_store"
                elif key in APP_DEPLOYMENT_KEYS:
                    storage = "deployment"
                else:
                    storage = "revision"
                sensitivity: Sensitivity = (
                    "secret"
                    if key in APP_SECRET_KEYS
                    else "private"
                    if key in APP_PRIVATE_KEYS
                    else "public"
                )
                apply_mode: ApplyMode = (
                    "deployment"
                    if storage == "deployment"
                    else "next_job"
                    if storage == "secret_store"
                    else "restart"
                    if key in APP_RESTART_KEYS
                    else "next_job"
                )
                tab, section, level = _app_ui(key)
                visible = True
            else:
                storage = "internal" if key in PIPELINE_INTERNAL_KEYS else "revision"
                annotation_args = get_args(field.annotation)
                sensitivity = (
                    "private" if field.annotation is Path or Path in annotation_args else "public"
                )
                apply_mode = "restart" if key in PIPELINE_RESTART_KEYS else "next_job"
                tab, section, level = _pipeline_ui(key)
                visible = key not in PIPELINE_INTERNAL_KEYS

            entries[key] = ConfigurationKey(
                key=key,
                owner=owner,
                validation_owner=model,
                scope="execution" if owner == "pipeline" else "application",
                storage=storage,
                sensitivity=sensitivity,
                apply_mode=apply_mode,
                title=_title(key),
                description=field.description,
                tab=tab,
                section=section,
                level=level,
                control=_control_for(model, key),
                visible=visible,
            )

    return entries


CONFIGURATION_CATALOG = _catalog()

_OWNER_BASES: dict[Owner, BaseModel] = {
    "app": settings,
    "pipeline": pipeline_settings,
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


def validate_database_values(
    values: dict[str, Any],
    *,
    allow_internal: bool = False,
) -> dict[str, Any]:
    """Validate a stored document and return normalized JSON values."""
    if not isinstance(values, dict):
        raise ConfigurationError("configuration values must be an object")

    for key in values:
        entry = CONFIGURATION_CATALOG.get(key)
        if entry is None:
            kind = "secret-like" if _SECRET_LIKE.search(key) else "unknown"
            raise ConfigurationError(f"{kind} configuration key is not allowed: {key}")
        if entry.storage == "internal" and allow_internal:
            continue
        if entry.storage == "secret_store":
            raise ConfigurationError(f"secret configuration key belongs in the secret store: {key}")
        if entry.storage != "revision":
            raise ConfigurationError(
                f"configuration key is {entry.storage}-owned and cannot be stored: {key}"
            )

    normalized: dict[str, Any] = {}
    for owner, base in _OWNER_BASES.items():
        owner_updates = {
            key: value for key, value in values.items() if CONFIGURATION_CATALOG[key].owner == owner
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

    app_values = {
        **_OWNER_BASES["app"].model_dump(),
        **{
            key: value
            for key, value in normalized.items()
            if CONFIGURATION_CATALOG[key].owner == "app"
        },
    }
    try:
        from marquee.core.path_utils import (  # noqa: PLC0415
            PathValidationError,
            validate_path_configuration,
        )

        validate_path_configuration(app_values)
    except PathValidationError as exc:
        raise ConfigurationError(f"invalid filesystem configuration: {exc}") from exc

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
    normalized = validate_database_values(revision.values, allow_internal=True)
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

    # Reject deployment, secret, and hidden compatibility keys at the mutation
    # boundary before validating the complete document. Existing internal keys
    # remain readable for one-release compatibility but cannot be changed.
    validate_database_values(updates)
    merged = {**current.values, **updates}
    normalized = validate_database_values(merged, allow_internal=True)
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


def legacy_environment_revision_updates(current_values: dict[str, Any]) -> dict[str, Any]:
    """Select explicit legacy environment values that are safe to seed into revisions."""
    updates: dict[str, Any] = {}
    for key, entry in CONFIGURATION_CATALOG.items():
        if entry.storage != "revision" or key in current_values:
            continue
        base = _OWNER_BASES[entry.owner]
        if key in base.model_fields_set:
            updates[key] = _json_value(getattr(base, key))
    return validate_database_values(updates)


async def import_legacy_revision_values(
    session: AsyncSession,
) -> tuple[ConfigurationState, int]:
    """Idempotently preserve resolved public environment behavior in one revision."""
    current = await read_current_configuration(session)
    updates = legacy_environment_revision_updates(current.values)
    if not updates:
        return current, 0
    state, changed = await update_configuration(
        session,
        expected_version=current.version,
        updates=updates,
        actor={"kind": "system", "id": "legacy_environment_import"},
        trigger="settings_migration",
    )
    return state, len(updates) if changed else 0
