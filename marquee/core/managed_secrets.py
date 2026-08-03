"""Encrypted, value-free-at-the-boundary storage for integration credentials."""

from __future__ import annotations

import base64
import binascii
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marquee.config import settings
from marquee.database import session_factory
from marquee.models.configuration import ManagedSecret, ManagedSecretEvent

MANAGED_SECRET_NAMES = (
    "TMDB_READ_ACCESS_TOKEN",
    "RADARR_API_KEY",
    "SONARR_API_KEY",
)
_KEY_BYTES = 32
_NONCE_BYTES = 12
_KEYRING_MAX_BYTES = 64 * 1024
_AAD_SCHEMA = 1


class ManagedSecretError(RuntimeError):
    """Base class for credential storage failures that are safe to show."""


class ManagedSecretUnavailableError(ManagedSecretError):
    """The external keyring or encryption runtime is unavailable."""


class ManagedSecretGenerationConflictError(ManagedSecretError):
    def __init__(self, current_generation: int) -> None:
        super().__init__("credential generation is stale")
        self.current_generation = current_generation


@dataclass(frozen=True, slots=True)
class ManagedSecretKeyring:
    active_key_id: str
    keys: dict[str, bytes]

    @classmethod
    def load(cls, path: Path | None = None) -> ManagedSecretKeyring:
        configured_path = path or settings.MARQUEE_SETTINGS_KEYRING_FILE
        if configured_path is None:
            raise ManagedSecretUnavailableError("settings encryption keyring is not configured")
        try:
            file_path = Path(configured_path)
            file_stat = file_path.stat()
        except OSError as exc:
            raise ManagedSecretUnavailableError(
                "settings encryption keyring is unreadable"
            ) from exc
        if not stat.S_ISREG(file_stat.st_mode):
            raise ManagedSecretUnavailableError(
                "settings encryption keyring must be a regular file"
            )
        if file_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ManagedSecretUnavailableError(
                "settings encryption keyring must not be group- or world-writable"
            )
        if file_stat.st_size > _KEYRING_MAX_BYTES:
            raise ManagedSecretUnavailableError("settings encryption keyring is too large")
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ManagedSecretUnavailableError("settings encryption keyring is invalid") from exc
        if not isinstance(payload, dict):
            raise ManagedSecretUnavailableError("settings encryption keyring is invalid")
        active_key_id = payload.get("active_key_id")
        encoded_keys = payload.get("keys")
        if not isinstance(active_key_id, str) or not active_key_id:
            raise ManagedSecretUnavailableError("settings encryption keyring has no active key")
        if not isinstance(encoded_keys, dict) or active_key_id not in encoded_keys:
            raise ManagedSecretUnavailableError(
                "settings encryption keyring has no active key material"
            )
        keys: dict[str, bytes] = {}
        for key_id, encoded in encoded_keys.items():
            if not isinstance(key_id, str) or not key_id or not isinstance(encoded, str):
                raise ManagedSecretUnavailableError("settings encryption keyring is invalid")
            try:
                material = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ManagedSecretUnavailableError(
                    "settings encryption keyring is invalid"
                ) from exc
            if len(material) != _KEY_BYTES:
                raise ManagedSecretUnavailableError(
                    "settings encryption keys must contain exactly 32 bytes"
                )
            keys[key_id] = material
        return cls(active_key_id=active_key_id, keys=keys)


@dataclass(frozen=True, slots=True)
class EncryptedSecret:
    ciphertext: bytes
    nonce: bytes
    key_id: str


class ManagedSecretCipher:
    def __init__(self, keyring: ManagedSecretKeyring) -> None:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError as exc:
            raise ManagedSecretUnavailableError(
                "the cryptography package required for managed credentials is unavailable"
            ) from exc
        self._aesgcm = AESGCM
        self._keyring = keyring

    @staticmethod
    def _aad(name: str, generation: int) -> bytes:
        return f"marquee-managed-secret:v{_AAD_SCHEMA}:{name}:{generation}".encode()

    def encrypt(self, *, name: str, generation: int, value: str) -> EncryptedSecret:
        nonce = os.urandom(_NONCE_BYTES)
        key_id = self._keyring.active_key_id
        ciphertext = self._aesgcm(self._keyring.keys[key_id]).encrypt(
            nonce,
            value.encode("utf-8"),
            self._aad(name, generation),
        )
        return EncryptedSecret(ciphertext=ciphertext, nonce=nonce, key_id=key_id)

    def decrypt(self, row: ManagedSecret) -> str:
        key = self._keyring.keys.get(row.key_id)
        if key is None:
            raise ManagedSecretUnavailableError(
                f"settings encryption key {row.key_id!r} is not present in the keyring"
            )
        try:
            plaintext = self._aesgcm(key).decrypt(
                row.nonce,
                row.ciphertext,
                self._aad(row.name, row.generation),
            )
            return plaintext.decode("utf-8")
        except Exception as exc:
            raise ManagedSecretUnavailableError(
                f"managed credential {row.name!r} failed authenticated decryption"
            ) from exc


def _validate_secret_name(name: str) -> None:
    if name not in MANAGED_SECRET_NAMES:
        raise ManagedSecretError("credential name is not managed by Marquee")


def _validate_secret_value(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ManagedSecretError("credential value must not be empty")
    if len(normalized) > 8192 or "\0" in normalized:
        raise ManagedSecretError("credential value is invalid")
    return normalized


async def replace_managed_secret(
    session: AsyncSession,
    *,
    name: str,
    value: str,
    expected_generation: int,
    actor: dict[str, Any],
    action: Literal["imported", "replaced", "rotated"] = "replaced",
) -> ManagedSecret:
    _validate_secret_name(name)
    normalized = _validate_secret_value(value)
    row = await session.scalar(
        select(ManagedSecret).where(ManagedSecret.name == name).with_for_update()
    )
    current_generation = row.generation if row is not None else 0
    if expected_generation != current_generation:
        raise ManagedSecretGenerationConflictError(current_generation)
    generation = current_generation + 1
    encrypted = ManagedSecretCipher(ManagedSecretKeyring.load()).encrypt(
        name=name,
        generation=generation,
        value=normalized,
    )
    if row is None:
        row = ManagedSecret(name=name, generation=generation)
        session.add(row)
    row.ciphertext = encrypted.ciphertext
    row.nonce = encrypted.nonce
    row.key_id = encrypted.key_id
    row.configured = True
    row.generation = generation
    session.add(
        ManagedSecretEvent(
            name=name,
            action=action,
            actor=actor,
            generation=generation,
        )
    )
    await session.flush()
    return row


async def clear_managed_secret(
    session: AsyncSession,
    *,
    name: str,
    expected_generation: int,
    actor: dict[str, Any],
) -> ManagedSecret:
    _validate_secret_name(name)
    row = await session.scalar(
        select(ManagedSecret).where(ManagedSecret.name == name).with_for_update()
    )
    current_generation = row.generation if row is not None else 0
    if expected_generation != current_generation:
        raise ManagedSecretGenerationConflictError(current_generation)
    generation = current_generation + 1
    encrypted = ManagedSecretCipher(ManagedSecretKeyring.load()).encrypt(
        name=name,
        generation=generation,
        value="",
    )
    if row is None:
        row = ManagedSecret(name=name, generation=generation)
        session.add(row)
    row.ciphertext = encrypted.ciphertext
    row.nonce = encrypted.nonce
    row.key_id = encrypted.key_id
    row.configured = False
    row.generation = generation
    session.add(
        ManagedSecretEvent(
            name=name,
            action="cleared",
            actor=actor,
            generation=generation,
        )
    )
    await session.flush()
    return row


async def managed_secret_statuses(session: AsyncSession) -> dict[str, dict[str, Any]]:
    rows = {
        row.name: row
        for row in (await session.scalars(select(ManagedSecret))).all()
        if row.name in MANAGED_SECRET_NAMES
    }
    statuses: dict[str, dict[str, Any]] = {}
    for name in MANAGED_SECRET_NAMES:
        row = rows.get(name)
        if row is not None:
            statuses[name] = {
                "configured": row.configured,
                "source": "managed",
                "generation": row.generation,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
        else:
            statuses[name] = {
                "configured": bool(getattr(settings, name)),
                "source": "environment" if getattr(settings, name) else "default",
                "generation": 0,
                "updated_at": None,
            }
    return statuses


async def read_effective_managed_secret(session: AsyncSession, name: str) -> str | None:
    _validate_secret_name(name)
    row = await session.get(ManagedSecret, name)
    if row is None:
        return getattr(settings, name)
    if not row.configured:
        return None
    return ManagedSecretCipher(ManagedSecretKeyring.load()).decrypt(row)


async def require_managed_secret_generation(
    session: AsyncSession,
    *,
    name: str,
    expected_generation: int,
    for_update: bool = False,
) -> ManagedSecret | None:
    """Check a credential generation without exposing or decrypting its value."""
    _validate_secret_name(name)
    statement = select(ManagedSecret).where(ManagedSecret.name == name)
    if for_update:
        statement = statement.with_for_update()
    row = await session.scalar(statement)
    current_generation = row.generation if row is not None else 0
    if expected_generation != current_generation:
        raise ManagedSecretGenerationConflictError(current_generation)
    return row


def managed_secret_store_health() -> dict[str, Any]:
    try:
        keyring = ManagedSecretKeyring.load()
        ManagedSecretCipher(keyring)
    except ManagedSecretUnavailableError as exc:
        return {"writable": False, "reason": str(exc)}
    return {"writable": True, "reason": None}


async def import_legacy_managed_secrets(session: AsyncSession) -> int:
    """One-time, idempotent import of legacy environment credentials."""
    ManagedSecretKeyring.load()
    existing = set((await session.scalars(select(ManagedSecret.name))).all())
    imported = 0
    for name in MANAGED_SECRET_NAMES:
        value = getattr(settings, name)
        if name in existing or not value:
            continue
        await replace_managed_secret(
            session,
            name=name,
            value=value,
            expected_generation=0,
            actor={"kind": "system", "id": "legacy_environment_import"},
            action="imported",
        )
        imported += 1
    return imported


async def rotate_managed_secrets(
    session: AsyncSession,
    *,
    actor: dict[str, Any],
) -> int:
    """Re-encrypt every row with the active key while preserving tombstones."""
    keyring = ManagedSecretKeyring.load()
    cipher = ManagedSecretCipher(keyring)
    rows = (await session.scalars(select(ManagedSecret).with_for_update())).all()
    rotated = 0
    expected_plaintext: dict[str, str] = {}
    for row in rows:
        value = cipher.decrypt(row)
        expected_plaintext[row.name] = value
        generation = row.generation + 1
        encrypted = cipher.encrypt(
            name=row.name,
            generation=generation,
            value=value,
        )
        row.ciphertext = encrypted.ciphertext
        row.nonce = encrypted.nonce
        row.key_id = encrypted.key_id
        row.generation = generation
        session.add(
            ManagedSecretEvent(
                name=row.name,
                action="rotated",
                actor=actor,
                generation=generation,
            )
        )
        rotated += 1
    await session.flush()
    for row in rows:
        if cipher.decrypt(row) != expected_plaintext[row.name]:
            raise ManagedSecretUnavailableError("managed credential rotation verification failed")
    return rotated


class ManagedSecretProvider:
    """Process-local decrypted view; database/keyring remain the authority."""

    def __init__(self) -> None:
        self._values: dict[str, str | None] = {
            name: getattr(settings, name) for name in MANAGED_SECRET_NAMES
        }
        self._generations: dict[str, int] = dict.fromkeys(MANAGED_SECRET_NAMES, 0)
        self._started = False

    @property
    def started(self) -> bool:
        return self._started

    def values(self) -> dict[str, str | None]:
        return dict(self._values)

    def generation(self, name: str) -> int:
        _validate_secret_name(name)
        return self._generations[name]

    def install_ephemeral(
        self,
        values: dict[str, str | None],
        *,
        generations: dict[str, int] | None = None,
    ) -> None:
        """Install an operation-scoped child view without opening the database or keyring."""
        unknown = set(values) - set(MANAGED_SECRET_NAMES)
        if unknown:
            raise ManagedSecretError("ephemeral credential set contains an unknown name")
        checked: dict[str, str | None] = dict.fromkeys(MANAGED_SECRET_NAMES)
        for name, value in values.items():
            checked[name] = _validate_secret_value(value) if value is not None else None
        generation_values = dict.fromkeys(MANAGED_SECRET_NAMES, 0)
        for name, generation in (generations or {}).items():
            _validate_secret_name(name)
            if not isinstance(generation, int) or isinstance(generation, bool) or generation < 0:
                raise ManagedSecretError("ephemeral credential generation is invalid")
            generation_values[name] = generation
        self._values = checked
        self._generations = generation_values
        self._started = True

    async def refresh(self, session: AsyncSession | None = None) -> None:
        if session is None:
            async with session_factory()() as owned_session:
                await self.refresh(owned_session)
            return
        rows = {
            row.name: row
            for row in (await session.scalars(select(ManagedSecret))).all()
            if row.name in MANAGED_SECRET_NAMES
        }
        values: dict[str, str | None] = {
            name: getattr(settings, name) for name in MANAGED_SECRET_NAMES
        }
        generations: dict[str, int] = dict.fromkeys(MANAGED_SECRET_NAMES, 0)
        if rows:
            cipher = ManagedSecretCipher(ManagedSecretKeyring.load())
            for name, row in rows.items():
                values[name] = cipher.decrypt(row) if row.configured else None
                generations[name] = row.generation
        self._values = values
        self._generations = generations
        self._started = True

    async def start(self) -> None:
        if self._started:
            return
        async with session_factory()() as session:
            try:
                await import_legacy_managed_secrets(session)
            except ManagedSecretUnavailableError:
                # No keyring is valid for installations that have not opted in.
                if (
                    settings.MARQUEE_SETTINGS_KEYRING_FILE is not None
                    or (await session.scalars(select(ManagedSecret.name).limit(1))).first()
                    is not None
                ):
                    raise
            else:
                await session.commit()
            await self.refresh(session)

    async def stop(self) -> None:
        """Release the process-local decrypted view on service shutdown."""
        self._values = dict.fromkeys(MANAGED_SECRET_NAMES)
        self._generations = dict.fromkeys(MANAGED_SECRET_NAMES, 0)
        self._started = False


managed_secret_provider = ManagedSecretProvider()
