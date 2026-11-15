"""Encrypted storage of master data (spec §6.2)."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from presidio_sidecar.registry_crypto import (
    FORMAT_HEADER,
    RegistryCipher,
    RegistryCryptoError,
    generate_registry_key,
    load_or_create_registry_key,
)


def test_aes_gcm_roundtrip_no_plaintext_in_payload() -> None:
    plaintext = b"Josef Brandstetter;Tischlerei Beispiel"
    cipher = RegistryCipher(generate_registry_key())

    encrypted = cipher.encrypt(plaintext)

    assert encrypted.startswith(FORMAT_HEADER)
    assert plaintext not in encrypted
    assert cipher.decrypt(encrypted) == plaintext


def test_each_encryption_uses_fresh_nonce() -> None:
    cipher = RegistryCipher(generate_registry_key())
    assert cipher.encrypt(b"gleicher Inhalt") != cipher.encrypt(b"gleicher Inhalt")


def test_manipulation_and_wrong_key_detected() -> None:
    encrypted = RegistryCipher(generate_registry_key()).encrypt(b"Stammdaten")
    manipulated = encrypted[:-1] + bytes([encrypted[-1] ^ 1])

    with pytest.raises(RegistryCryptoError):
        RegistryCipher(generate_registry_key()).decrypt(encrypted)
    with pytest.raises(RegistryCryptoError):
        RegistryCipher(generate_registry_key()).decrypt(manipulated)


@pytest.mark.parametrize("key", [b"", b"zu kurz", b"x" * 16, b"x" * 31, b"x" * 33])
def test_only_aes_256_keys_accepted(key: bytes) -> None:
    with pytest.raises(RegistryCryptoError):
        RegistryCipher(key)


def test_registry_key_created_atomically_with_0600(tmp_path: Path) -> None:
    path = tmp_path / "state" / "registry.key"

    created = load_or_create_registry_key(path)

    assert len(created) == 32
    assert path.read_bytes() == created
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600
    assert load_or_create_registry_key(path) == created


def test_parallel_generation_provides_common_key(tmp_path: Path) -> None:
    path = tmp_path / "registry.key"

    with ThreadPoolExecutor(max_workers=8) as executor:
        keys = list(executor.map(lambda _: load_or_create_registry_key(path), range(16)))

    assert len(set(keys)) == 1
    assert keys[0] == path.read_bytes()
    assert not list(tmp_path.glob(".registry.key.*.tmp"))


def test_invalid_existing_key_not_overwritten(
    tmp_path: Path,
) -> None:
    path = tmp_path / "registry.key"
    invalid = b"bestehend, aber zu kurz"
    path.write_bytes(invalid)
    if os.name == "posix":
        path.chmod(0o600)

    with pytest.raises(RegistryCryptoError, match="genau 32 Bytes"):
        load_or_create_registry_key(path)

    assert path.read_bytes() == invalid


@pytest.mark.skipif(os.name != "posix", reason="POSIX-Dateirechte")
def test_insecure_permissions_are_rejected_not_repaired(tmp_path: Path) -> None:
    path = tmp_path / "registry.key"
    key = generate_registry_key()
    path.write_bytes(key)
    path.chmod(0o644)

    with pytest.raises(RegistryCryptoError, match="0600"):
        load_or_create_registry_key(path)

    assert path.read_bytes() == key
    assert path.stat().st_mode & 0o777 == 0o644


@pytest.mark.skipif(os.name != "posix", reason="Symlink-Semantik")
def test_symlink_not_accepted_as_key_file(tmp_path: Path) -> None:
    target = tmp_path / "target.key"
    target.write_bytes(generate_registry_key())
    target.chmod(0o600)
    link = tmp_path / "registry.key"
    link.symlink_to(target)

    with pytest.raises(RegistryCryptoError):
        load_or_create_registry_key(link)
