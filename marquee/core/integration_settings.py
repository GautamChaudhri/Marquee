"""Transactionally coherent integration URL and credential snapshots."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.core.configuration import (
    CONFIGURATION_SCHEMA_VERSION,
    ConfigurationUnavailableError,
    configuration_checksum,
    validate_database_values,
)
from marquee.core.managed_secrets import (
    MANAGED_SECRET_NAMES,
    ManagedSecretCipher,
    ManagedSecretKeyring,
)
from marquee.models.configuration import ConfigurationCurrent, ConfigurationRevision, ManagedSecret


@dataclass(frozen=True, slots=True)
class IntegrationCapability:
    provider: str
    configuration_version: int
    url: str | None
    credential: str | None
    secret_generation: int
    secret_source: str


@dataclass(frozen=True, slots=True)
class IntegrationSettingsSnapshot:
    configuration_version: int
    checksum: str
    capabilities: dict[str, IntegrationCapability]

    def for_provider(self, provider: str) -> IntegrationCapability:
        try:
            return self.capabilities[provider]
        except KeyError as exc:
            raise ValueError(f"unknown integration provider: {provider}") from exc


_PROVIDERS = {
    "tmdb": (None, "TMDB_READ_ACCESS_TOKEN"),
    "radarr": ("RADARR_URL", "RADARR_API_KEY"),
    "sonarr": ("SONARR_URL", "SONARR_API_KEY"),
}


async def read_integration_settings_snapshot(
    session: AsyncSession,
) -> IntegrationSettingsSnapshot:
    """Read the current revision and every managed credential in one SQL snapshot."""
    statement = (
        select(ConfigurationRevision, ManagedSecret)
        .select_from(ConfigurationCurrent)
        .join(
            ConfigurationRevision,
            ConfigurationRevision.version == ConfigurationCurrent.current_version,
        )
        .outerjoin(ManagedSecret, ManagedSecret.name.in_(MANAGED_SECRET_NAMES))
        .where(ConfigurationCurrent.singleton_id == 1)
    )
    rows = (await session.execute(statement)).all()
    if not rows:
        raise ConfigurationUnavailableError("configuration_current singleton is missing")
    revision = rows[0][0]
    normalized = validate_database_values(revision.values, allow_internal=True)
    checksum = configuration_checksum(normalized)
    if revision.schema_version != CONFIGURATION_SCHEMA_VERSION or revision.checksum != checksum:
        raise ConfigurationUnavailableError(
            f"configuration revision {revision.version} failed schema/checksum validation"
        )

    secret_rows = {row[1].name: row[1] for row in rows if row[1] is not None}
    cipher = (
        ManagedSecretCipher(ManagedSecretKeyring.load())
        if any(row.configured for row in secret_rows.values())
        else None
    )
    capabilities: dict[str, IntegrationCapability] = {}
    for provider, (url_key, secret_name) in _PROVIDERS.items():
        secret_row = secret_rows.get(secret_name)
        if secret_row is None:
            credential = getattr(settings, secret_name)
            generation = 0
            source = "environment" if secret_name in settings.model_fields_set else "default"
        else:
            credential = (
                cipher.decrypt(secret_row) if secret_row.configured and cipher is not None else None
            )
            generation = secret_row.generation
            source = "managed"
        capabilities[provider] = IntegrationCapability(
            provider=provider,
            configuration_version=revision.version,
            url=(
                str(normalized.get(url_key, getattr(settings, url_key)))
                if url_key and normalized.get(url_key, getattr(settings, url_key))
                else None
            ),
            credential=credential,
            secret_generation=generation,
            secret_source=source,
        )
    return IntegrationSettingsSnapshot(
        configuration_version=revision.version,
        checksum=checksum,
        capabilities=capabilities,
    )
