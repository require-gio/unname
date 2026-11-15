"""Authenticated encryption for the master-data registry.

The AES key is stored as a private file (POSIX ``0600``) in the protected
app state, and the registry image is stored solely as an AES-256-GCM
ciphertext. This protects a single copied registry file and detects
tampering; a full copy of the user-state directory is not a separate
key-at-rest scenario. A platform keychain provider can later replace this
well-defined storage without changing the ciphertext format.
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_SIZE = 32
NONCE_SIZE = 12

# Header combines version ID and additional authenticated data.
# Prevents a ciphertext from another file format being mistakenly
# interpreted as a registry.
FORMAT_HEADER = b"PRESIDIO-REGISTRY\x01"


class RegistryCryptoError(ValueError):
    """Invalid registry ciphertext or its key."""


def generate_registry_key() -> bytes:
    """Generate a new AES-256 key.

    The returned key must be stored separately from the ciphertext
    with restrictive access.
    """

    return AESGCM.generate_key(bit_length=256)


def load_or_create_registry_key(path: Path) -> bytes:
    """Load a secure key or create it atomically.

    An existing file is never repaired or replaced. On wrong length,
    wrong owner, insecure POSIX permissions, or wrong file type the
    function aborts, preventing a silent config error that would make
    the registry unreadable.
    """

    key_path = Path(path)
    try:
        return _load_registry_key(key_path)
    except FileNotFoundError:
        pass

    key_path.parent.mkdir(parents=True, exist_ok=True)
    key = generate_registry_key()
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=key_path.parent,
            prefix=f".{key_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            os.chmod(temporary_path, 0o600)
            stream.write(key)
            stream.flush()
            os.fsync(stream.fileno())

        try:
            # A hard link publishes the fully written inode atomically and fails
            # if the target was created by another process in the meantime.
            os.link(temporary_path, key_path, follow_symlinks=False)
        except FileExistsError:
            return _load_registry_key(key_path)
        except OSError as error:
            raise RegistryCryptoError(
                f"Registry-Schluessel {key_path} konnte nicht atomar angelegt werden."
            ) from error

        loaded = _load_registry_key(key_path)
        if loaded != key:
            raise RegistryCryptoError(
                "Der neu angelegte Registry-Schluessel wurde unerwartet veraendert."
            )
        return loaded
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _load_registry_key(path: Path) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError as error:
        raise RegistryCryptoError(
            f"Registry-Schluessel {path} konnte nicht sicher geoeffnet werden."
        ) from error

    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RegistryCryptoError(f"Registry-Schluessel {path} ist keine regulaere Datei.")
        if os.name == "posix":
            mode = stat.S_IMODE(metadata.st_mode)
            if mode != 0o600:
                raise RegistryCryptoError(
                    f"Registry-Schluessel {path} braucht POSIX-Rechte 0600, hat aber {mode:04o}."
                )
            if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
                raise RegistryCryptoError(
                    f"Registry-Schluessel {path} gehoert nicht dem aktuellen Benutzer."
                )

        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            key = stream.read(KEY_SIZE + 1)
    except OSError as error:
        raise RegistryCryptoError(
            f"Registry-Schluessel {path} konnte nicht gelesen werden."
        ) from error
    finally:
        os.close(descriptor)

    if len(key) != KEY_SIZE:
        raise RegistryCryptoError(
            f"Registry-Schluessel {path} muss genau {KEY_SIZE} Bytes enthalten."
        )
    return key


class RegistryCipher:
    """Small version-bound AES-256-GCM shell."""

    def __init__(self, key: bytes) -> None:
        if not isinstance(key, bytes) or len(key) != KEY_SIZE:
            raise RegistryCryptoError(
                f"Der Registry-Schluessel muss genau {KEY_SIZE} Bytes lang sein."
            )
        self._cipher = AESGCM(key)

    def encrypt(self, plaintext: bytes) -> bytes:
        """Encrypt bytes with a fresh nonce."""

        if not isinstance(plaintext, bytes):
            raise TypeError("plaintext muss bytes sein.")
        nonce = os.urandom(NONCE_SIZE)
        ciphertext = self._cipher.encrypt(nonce, plaintext, FORMAT_HEADER)
        return FORMAT_HEADER + nonce + ciphertext

    def decrypt(self, payload: bytes) -> bytes:
        """Decrypt and authenticate a registry ciphertext."""

        minimum_size = len(FORMAT_HEADER) + NONCE_SIZE + 16  # GCM-Tag
        if (
            not isinstance(payload, bytes)
            or len(payload) < minimum_size
            or not payload.startswith(FORMAT_HEADER)
        ):
            raise RegistryCryptoError("Unbekanntes oder unvollstaendiges Registry-Format.")

        nonce_start = len(FORMAT_HEADER)
        nonce_end = nonce_start + NONCE_SIZE
        nonce = payload[nonce_start:nonce_end]
        ciphertext = payload[nonce_end:]
        try:
            return self._cipher.decrypt(nonce, ciphertext, FORMAT_HEADER)
        except InvalidTag as error:
            # Do not distinguish wrong key from tampering: both must never result in partially
            # loaded data.
            raise RegistryCryptoError(
                "Registry konnte nicht authentifiziert oder entschluesselt werden."
            ) from error
