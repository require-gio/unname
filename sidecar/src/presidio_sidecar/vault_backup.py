"""Password-protected backup of the translation table.

"Take to another device": table **and** key stored together.
Without the key the table on another device is useless – the same name gets a
different code there, and new codes don’t match old outputs.

The file therefore contains real names and the secret. It is written encrypted
with a password: scrypt derives an AES-256-GCM key; header, salt and scrypt
parameters are Additional Authenticated Data. A tampered or truncated file
or a wrong password are detected equally when opening – the error message
does not reveal which case occurred.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

HEADER: Final = b"UNNAME-VAULT-BACKUP\x01"
FORMAT: Final = "unname-vault-backup"
VERSION: Final = 1
SALT_SIZE: Final = 16
NONCE_SIZE: Final = 12
#: scrypt cost: 2^15 with r = 8 uses 32 MiB and runs under a second on a laptop – suitable for a
#: rarely opened file.
SCRYPT_LOG_N: Final = 15
SCRYPT_R: Final = 8
SCRYPT_P: Final = 1
MIN_PASSWORD_LENGTH: Final = 10
MAX_PASSWORD_LENGTH: Final = 1024
#: max size for a backup to read; a table with 100 k entries is far smaller.
MAX_BACKUP_BYTES: Final = 64 * 1024 * 1024


class VaultBackupError(ValueError):
    """The backup is invalid, corrupted, or the password is wrong.

    ``code`` maps to UI messages: ``password_too_short``,
    ``invalid_backup`` (format, size, content) and ``wrong_password``
    (password wrong *or* file altered – cannot distinguish).
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class BackupContent:
    secret: bytes = field(repr=False)
    #: Serialized SQLite database of the table (plain text!).
    database: bytes = field(repr=False)
    created_at: str


def check_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise VaultBackupError(
            "password_too_short",
            f"Das Passwort braucht mindestens {MIN_PASSWORD_LENGTH} Zeichen.",
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise VaultBackupError("password_too_long", "Das Passwort ist zu lang.")


def seal(secret: bytes, database: bytes, password: str, *, now: datetime | None = None) -> bytes:
    """Encrypt secret and table with the password."""

    check_password(password)
    salt = os.urandom(SALT_SIZE)
    params = bytes((SCRYPT_LOG_N, SCRYPT_R, SCRYPT_P))
    document = json.dumps(
        {
            "format": FORMAT,
            "version": VERSION,
            "created_at": (now or datetime.now(UTC)).isoformat(),
            "secret": base64.b64encode(secret).decode("ascii"),
            "database": base64.b64encode(database).decode("ascii"),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    nonce = os.urandom(NONCE_SIZE)
    associated = HEADER + salt + params
    key = _derive(password, salt, SCRYPT_LOG_N, SCRYPT_R, SCRYPT_P)
    return associated + nonce + AESGCM(key).encrypt(nonce, document, associated)


def open_backup(payload: bytes, password: str) -> BackupContent:
    """Validate and decrypt a backup."""

    if not isinstance(payload, bytes | bytearray) or len(payload) > MAX_BACKUP_BYTES:
        raise VaultBackupError("invalid_backup", "Die Datei ist keine gueltige Sicherung.")
    payload = bytes(payload)
    head = len(HEADER) + SALT_SIZE + 3
    if len(payload) < head + NONCE_SIZE + 16 or not payload.startswith(HEADER):
        raise VaultBackupError("invalid_backup", "Die Datei ist keine gueltige Sicherung.")
    if not isinstance(password, str) or not password or len(password) > MAX_PASSWORD_LENGTH:
        raise VaultBackupError("wrong_password", "Passwort falsch oder Datei veraendert.")
    salt = payload[len(HEADER) : len(HEADER) + SALT_SIZE]
    log_n, r, p = payload[len(HEADER) + SALT_SIZE : head]
    # Only accept our own parameters: a malicious file could otherwise demand arbitrary memory and
    # CPU.
    if (log_n, r, p) != (SCRYPT_LOG_N, SCRYPT_R, SCRYPT_P):
        raise VaultBackupError("invalid_backup", "Die Datei ist keine gueltige Sicherung.")
    nonce = payload[head : head + NONCE_SIZE]
    key = _derive(password, salt, log_n, r, p)
    try:
        document = AESGCM(key).decrypt(nonce, payload[head + NONCE_SIZE :], payload[:head])
    except InvalidTag as error:
        raise VaultBackupError(
            "wrong_password", "Passwort falsch oder Datei veraendert."
        ) from error
    try:
        raw = json.loads(document)
        if raw.get("format") != FORMAT or raw.get("version") != VERSION:
            raise ValueError("Format")
        secret = base64.b64decode(raw["secret"], validate=True)
        database = base64.b64decode(raw["database"], validate=True)
        created_at = str(raw.get("created_at") or "")
    except (KeyError, TypeError, ValueError, UnicodeError) as error:
        raise VaultBackupError("invalid_backup", "Die Sicherung ist unvollstaendig.") from error
    return BackupContent(secret=secret, database=database, created_at=created_at)


def _derive(password: str, salt: bytes, log_n: int, r: int, p: int) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**log_n, r=r, p=p).derive(password.encode("utf-8"))


__all__ = [
    "MAX_BACKUP_BYTES",
    "MIN_PASSWORD_LENGTH",
    "BackupContent",
    "VaultBackupError",
    "check_password",
    "open_backup",
    "seal",
]
