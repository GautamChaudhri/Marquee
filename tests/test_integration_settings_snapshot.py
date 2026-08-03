from __future__ import annotations

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.core.integration_settings import read_integration_settings_snapshot
from marquee.models.configuration import ManagedSecret


@pytest.mark.asyncio
async def test_integration_snapshot_uses_one_configuration_revision(
    db: AsyncSession,
) -> None:
    await db.execute(delete(ManagedSecret))

    snapshot = await read_integration_settings_snapshot(db)

    assert snapshot.configuration_version > 0
    assert len(snapshot.checksum) == 64
    assert set(snapshot.capabilities) == {"tmdb", "radarr", "sonarr"}
    assert {capability.configuration_version for capability in snapshot.capabilities.values()} == {
        snapshot.configuration_version
    }


@pytest.mark.asyncio
async def test_integration_snapshot_honors_managed_secret_tombstone(
    db: AsyncSession,
) -> None:
    await db.execute(delete(ManagedSecret))
    db.add(
        ManagedSecret(
            name="RADARR_API_KEY",
            ciphertext=b"",
            nonce=b"\0" * 12,
            key_id="retired",
            configured=False,
            generation=4,
        )
    )
    await db.flush()

    snapshot = await read_integration_settings_snapshot(db)
    radarr = snapshot.for_provider("radarr")

    assert radarr.credential is None
    assert radarr.secret_source == "managed"
    assert radarr.secret_generation == 4
