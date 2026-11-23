from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from marquee.core.managed_secrets import (  # noqa: E402
    ManagedSecretCipher,
    ManagedSecretKeyring,
    ManagedSecretUnavailableError,
)
from marquee.maintenance import _generate_settings_keyring  # noqa: E402
from marquee.models.configuration import ManagedSecret, ManagedSecretEvent  # noqa: E402


def _write_keyring(path: Path, *, active: str, keys: dict[str, bytes]) -> Path:
    path.write_text(
        json.dumps(
            {
                "active_key_id": active,
                "keys": {
                    key_id: base64.b64encode(material).decode("ascii")
                    for key_id, material in keys.items()
                },
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _row(*, ciphertext: bytes, nonce: bytes, key_id: str, generation: int = 1):
    return ManagedSecret(
        name="RADARR_API_KEY",
        ciphertext=ciphertext,
        nonce=nonce,
        key_id=key_id,
        configured=True,
        generation=generation,
    )


def test_aes_gcm_round_trip_uses_unique_96_bit_nonces(tmp_path):
    keyring = ManagedSecretKeyring.load(
        _write_keyring(tmp_path / "keyring.json", active="2026-a", keys={"2026-a": os.urandom(32)})
    )
    cipher = ManagedSecretCipher(keyring)
    first = cipher.encrypt(name="RADARR_API_KEY", generation=1, value="credential-value")
    second = cipher.encrypt(name="RADARR_API_KEY", generation=1, value="credential-value")

    assert len(first.nonce) == len(second.nonce) == 12
    assert first.nonce != second.nonce
    assert first.ciphertext != second.ciphertext
    assert b"credential-value" not in first.ciphertext
    assert (
        cipher.decrypt(_row(ciphertext=first.ciphertext, nonce=first.nonce, key_id=first.key_id))
        == "credential-value"
    )


@pytest.mark.parametrize("mutation", ["ciphertext", "nonce", "name", "generation"])
def test_authenticated_decryption_rejects_tampering_and_aad_mismatch(tmp_path, mutation):
    keyring = ManagedSecretKeyring.load(
        _write_keyring(tmp_path / "keyring.json", active="2026-a", keys={"2026-a": os.urandom(32)})
    )
    cipher = ManagedSecretCipher(keyring)
    encrypted = cipher.encrypt(name="RADARR_API_KEY", generation=1, value="credential-value")
    row = _row(
        ciphertext=encrypted.ciphertext,
        nonce=encrypted.nonce,
        key_id=encrypted.key_id,
    )
    if mutation == "ciphertext":
        row.ciphertext = bytes([row.ciphertext[0] ^ 1]) + row.ciphertext[1:]
    elif mutation == "nonce":
        row.nonce = bytes([row.nonce[0] ^ 1]) + row.nonce[1:]
    elif mutation == "name":
        row.name = "SONARR_API_KEY"
    else:
        row.generation = 2

    with pytest.raises(ManagedSecretUnavailableError, match="authenticated decryption"):
        cipher.decrypt(row)


def test_keyring_permissions_and_key_lengths_fail_closed(tmp_path):
    writable = _write_keyring(
        tmp_path / "writable.json", active="active", keys={"active": os.urandom(32)}
    )
    writable.chmod(0o666)
    with pytest.raises(ManagedSecretUnavailableError, match="group- or world-writable"):
        ManagedSecretKeyring.load(writable)

    short = _write_keyring(tmp_path / "short.json", active="active", keys={"active": b"short"})
    with pytest.raises(ManagedSecretUnavailableError, match="exactly 32 bytes"):
        ManagedSecretKeyring.load(short)


def test_key_rotation_keeps_old_key_until_new_ciphertext_is_verified(tmp_path):
    old_material = os.urandom(32)
    new_material = os.urandom(32)
    old_cipher = ManagedSecretCipher(
        ManagedSecretKeyring.load(
            _write_keyring(tmp_path / "old.json", active="old", keys={"old": old_material})
        )
    )
    encrypted = old_cipher.encrypt(name="RADARR_API_KEY", generation=1, value="credential-value")
    old_row = _row(
        ciphertext=encrypted.ciphertext,
        nonce=encrypted.nonce,
        key_id=encrypted.key_id,
    )

    rotating_cipher = ManagedSecretCipher(
        ManagedSecretKeyring.load(
            _write_keyring(
                tmp_path / "rotating.json",
                active="new",
                keys={"old": old_material, "new": new_material},
            )
        )
    )
    plaintext = rotating_cipher.decrypt(old_row)
    rotated = rotating_cipher.encrypt(name="RADARR_API_KEY", generation=2, value=plaintext)
    assert rotated.key_id == "new"
    assert (
        rotating_cipher.decrypt(
            _row(
                ciphertext=rotated.ciphertext,
                nonce=rotated.nonce,
                key_id=rotated.key_id,
                generation=2,
            )
        )
        == "credential-value"
    )

    with pytest.raises(ManagedSecretUnavailableError, match="not present"):
        old_cipher.decrypt(
            _row(
                ciphertext=rotated.ciphertext,
                nonce=rotated.nonce,
                key_id=rotated.key_id,
                generation=2,
            )
        )


def test_secret_audit_model_has_no_value_ciphertext_or_fingerprint_columns():
    assert {column.name for column in ManagedSecretEvent.__table__.columns}.isdisjoint(
        {"value", "ciphertext", "nonce", "fingerprint"}
    )


def test_keyring_generator_uses_exclusive_0600_file(tmp_path):
    path = tmp_path / "private" / "settings-keyring.json"
    result = _generate_settings_keyring(path)
    assert result["created"] is True
    assert path.stat().st_mode & 0o777 == 0o600
    assert ManagedSecretKeyring.load(path).active_key_id == "v1"
    with pytest.raises(FileExistsError):
        _generate_settings_keyring(path)
