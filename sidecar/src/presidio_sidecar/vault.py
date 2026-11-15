"""Local token vault for deterministic and reversible replacements.

Determinism and reverse path are intentionally separate:

* The private secret is enough to generate stable HMAC short codes.
* Only ``reversible=True`` stores a mapping in a SQLite database.
* ``prepare_token``/``prepare_surrogate`` compute a mapping without writing
  (dry-run). ``commit`` persists it – “saving is the commit”.
  Repeated toggling then discarding leaves no trace.

The SQLite database lives in memory while the sidecar runs. On disk only its
AES-256-GCM encrypted serialization is stored, protecting clear values,
normalised forms, indexes and metadata from offline inspection.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import sqlite3
import stat
import subprocess
import tempfile
import threading
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from faker import Faker

from . import vault_backup
from .languages import PACKS

SECRET_SIZE: Final = 32
NONCE_SIZE: Final = 12
TOKEN_HEX_LENGTH: Final = 8
TOKEN_PATTERN: Final = re.compile(r"<<(?P<entity>[A-Z][A-Z0-9_]*?)_(?P<digest>[0-9a-f]{8})>>")
DATABASE_HEADER: Final = b"PRESIDIO-TOKEN-VAULT\x01"
#: 2: origin per short code (``usage``). 3: ``last_used_at`` per short code – basis for the
#: retention rule “delete unused entries” (spec §3.6).
DATABASE_SCHEMA_VERSION: Final = 3
ROTATION_WARNING: Final = (
    "Bei einem Schlüsselwechsel ändern sich alle Kürzel. "
    "Alte Ausgaben lassen sich danach nicht mehr auflösen."
)

_WHITESPACE = re.compile(r"\s+")
_ENTITY_ALIASES = {
    "ORG": "ORGANIZATION",
    "PER": "PERSON",
    "LOC": "LOCATION",
}


class VaultError(RuntimeError):
    """The vault could not perform an operation safely."""


class VaultCryptoError(VaultError):
    """Secret or encrypted mapping is invalid."""


class VaultCollisionError(VaultError):
    """A short HMAC or a surrogate name would not be unique."""


@dataclass(frozen=True, slots=True)
class DeanonymizeResult:
    text: str
    resolved: int
    unknown: int
    unknown_unique: int


@dataclass(frozen=True, slots=True)
class TokenRecord:
    """What the vault knows about a short code – for restore.

    ``origins`` lists, for each entry, when the short code was stored
    (oldest first). Empty when it entered the table without an entry
    (legacy UI).
    """

    entity_type: str
    canonical: str = field(repr=False)
    origins: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class PendingMapping:
    """A computed but not yet persisted mapping (dry-run).

    Exists only in the sidecar’s memory and never appears in an API response;
    ``repr`` therefore omits clear values.
    """

    token: str
    entity_type: str
    normalized_value: str = field(repr=False)
    canonical: str = field(repr=False)
    surrogate: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class VaultStatus:
    fingerprint: str
    created_at: str
    secret_permissions: str
    mapping_persisted: bool
    mapping_count: int
    #: How many history entries the mappings originate from
    #: (“418 mappings from 12 entries”); mappings from the legacy UI are excluded
    #: because they have no entry.
    entry_count: int = 0
    storage_scope: str = "private_app_state"
    rotation_warning: str = ROTATION_WARNING

    def as_dict(self) -> dict[str, str | bool | int]:
        return {
            "state": "ready",
            "key_fingerprint": self.fingerprint,
            "warning": self.rotation_warning,
            "fingerprint": self.fingerprint,
            "created_at": self.created_at,
            "secret_permissions": self.secret_permissions,
            "mapping_persisted": self.mapping_persisted,
            "mapping_count": self.mapping_count,
            "entry_count": self.entry_count,
            "storage_scope": self.storage_scope,
            "rotation_warning": self.rotation_warning,
        }


def normalize_value(value: str) -> str:
    """Normalize exactly the input that goes into the HMAC."""

    if not isinstance(value, str):
        raise TypeError("Tresorwerte muessen Text sein.")
    normalized = _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", value)).strip().casefold()
    if not normalized:
        raise ValueError("Ein leerer Wert kann nicht pseudonymisiert werden.")
    return normalized


def normalize_entity_type(entity_type: str) -> str:
    if not isinstance(entity_type, str):
        raise TypeError("Entitaetstyp muss Text sein.")
    normalized = entity_type.strip().upper().replace("-", "_").replace(" ", "_")
    normalized = _ENTITY_ALIASES.get(normalized, normalized)
    if not normalized or not re.fullmatch(r"[A-Z][A-Z0-9_]*", normalized):
        raise ValueError("Ungueltiger Entitaetstyp.")
    return normalized


def canonical_rank(value: str) -> int:
    """Mixed case wins over all-caps or all-lowercase."""

    cased = [character for character in value if character.isalpha()]
    if not cased:
        return 0
    has_upper = any(character.isupper() for character in cased)
    has_lower = any(character.islower() for character in cased)
    if has_upper and has_lower:
        return 3
    if has_upper:
        return 2
    return 1


class TokenVault:
    """Thread-safe vault stored in a private app-state directory."""

    def __init__(self, state_directory: Path) -> None:
        self.state_directory = Path(state_directory)
        _reject_project_state_directory(self.state_directory)
        _prepare_private_directory(self.state_directory)
        self.secret_path = self.state_directory / "token-vault.secret"
        self.mapping_path = self.state_directory / "token-vault.sqlite.enc"
        self._secret = _load_or_create_secret(self.secret_path)
        self._mapping: _EncryptedSqliteMapping | None = None
        self._lock = threading.RLock()

    @property
    def encryption_key(self) -> bytes:
        """Separate, stable AES key for Presidio encrypt/decrypt."""

        return _derive_key(self._secret, b"presidio-native-encrypt-v1")

    @property
    def hash_salt(self) -> bytes:
        """Stable, non-exported salt for Presidio's hash operator."""

        return _derive_key(self._secret, b"presidio-one-way-hash-v1")

    def token_for(self, entity_type: str, value: str, *, reversible: bool) -> str:
        entity = normalize_entity_type(entity_type)
        normalized = normalize_value(value)
        digest = self._token_digest(entity, normalized)
        token = f"<<{entity}_{digest[:TOKEN_HEX_LENGTH]}>>"
        if reversible:
            with self._lock:
                self._mapping_store().remember(
                    token=token,
                    entity_type=entity,
                    normalized_value=normalized,
                    canonical=value,
                )
        return token

    def prepare_token(self, entity_type: str, value: str) -> PendingMapping:
        """Compute a token and its mapping without touching the vault."""

        entity = normalize_entity_type(entity_type)
        normalized = normalize_value(value)
        digest = self._token_digest(entity, normalized)
        return PendingMapping(
            token=f"<<{entity}_{digest[:TOKEN_HEX_LENGTH]}>>",
            entity_type=entity,
            normalized_value=normalized,
            canonical=value,
        )

    def prepare_surrogate(
        self,
        entity_type: str,
        value: str,
        *,
        reserved: dict[str, str] | None = None,
        language: str = "de",
    ) -> PendingMapping:
        """Choose a surrogate name without persisting it.

        ``language`` selects the origin of invented names (e.g. language of the
        paragraph). If a token already has a surrogate, it stays the same across
        all languages: the same name always maps to the same surrogate.

        ``reserved`` maps tokens already assigned a surrogate in this run.
        Without this lock two values from the same document could receive the
        same surrogate, causing a conflict only at commit time.
        """

        pending = self.prepare_token(entity_type, value)
        taken = reserved if reserved is not None else {}
        with self._lock:
            mapping = self._existing_mapping_store()
            existing = mapping.surrogate_for_token(pending.token) if mapping is not None else None
            if existing is not None:
                return _with_surrogate(pending, existing)
            for attempt in range(100):
                surrogate = self._generate_surrogate(
                    pending.entity_type, pending.token, attempt, language
                )
                if normalize_value(surrogate) == pending.normalized_value:
                    continue
                if taken.get(surrogate, pending.token) != pending.token:
                    continue
                if mapping is None or mapping.surrogate_available(surrogate, token=pending.token):
                    taken[surrogate] = pending.token
                    return _with_surrogate(pending, surrogate)
        raise VaultCollisionError("Es konnte kein eindeutiger Deckname erzeugt werden.")

    def commit(
        self,
        pending: tuple[PendingMapping, ...] | list[PendingMapping],
        *,
        entry_id: str | None = None,
        used_at: datetime | None = None,
    ) -> int:
        """Writes calculated mappings as a single vault state.

        With ``entry_id`` the vault also records which entry a token was stored in
        (origin for restore). Returns the number of distinct tokens.
        """

        items = tuple(pending)
        if not all(isinstance(item, PendingMapping) for item in items):
            raise TypeError("commit erwartet ausschliesslich PendingMapping-Eintraege.")
        if entry_id is not None and (not isinstance(entry_id, str) or not entry_id.strip()):
            raise ValueError("entry_id muss eine nicht-leere Zeichenkette sein.")
        if not items:
            return 0
        timestamp = (used_at or datetime.now(UTC)).isoformat()
        with self._lock:
            return self._mapping_store().remember_many(
                items,
                entry_id=entry_id,
                used_at=timestamp,
            )

    def token_origins(self, token: str) -> tuple[tuple[str, str], ...]:
        """``(entry_id, used_at)`` per entry where a token was stored."""

        with self._lock:
            mapping = self._existing_mapping_store()
            return mapping.usage_for_token(token) if mapping is not None else ()

    def lookup(self, tokens: set[str] | frozenset[str]) -> dict[str, TokenRecord]:
        """Clear value, type and origin of known tokens – read-only.

        Resolving each token individually would be too slow; unknown tokens are omitted.
        """

        if not tokens:
            return {}
        with self._lock:
            mapping = self._existing_mapping_store()
            return mapping.lookup(tokens) if mapping is not None else {}

    def surrogate_index(self) -> tuple[tuple[str, str], ...]:
        """``(token, surrogate)`` of all stored surrogates, longest first."""

        with self._lock:
            mapping = self._existing_mapping_store()
            return mapping.surrogate_tokens() if mapping is not None else ()

    def touch(self, tokens: set[str] | frozenset[str], *, used_at: datetime | None = None) -> int:
        """Records that tokens were just used (for restore).

        Only the timestamp changes, not the value. It supports the retention policy
        “delete unused entries after 6/12 months”. Returns the number of known tokens.
        """

        if not tokens:
            return 0
        timestamp = (used_at or datetime.now(UTC)).isoformat()
        with self._lock:
            mapping = self._existing_mapping_store()
            return mapping.touch(tokens, used_at=timestamp) if mapping is not None else 0

    def last_used(self, token: str) -> str | None:
        with self._lock:
            mapping = self._existing_mapping_store()
            return mapping.last_used(token) if mapping is not None else None

    def surrogate_for(self, entity_type: str, value: str) -> tuple[str, str]:
        """Returns ``(token, surrogate)``; surrogates require the vault."""

        entity = normalize_entity_type(entity_type)
        token = self.token_for(entity, value, reversible=True)
        with self._lock:
            mapping = self._mapping_store()
            existing = mapping.surrogate_for_token(token)
            if existing is not None:
                return token, existing

            for attempt in range(100):
                surrogate = self._generate_surrogate(entity, token, attempt)
                if normalize_value(surrogate) == normalize_value(value):
                    continue
                if mapping.surrogate_available(surrogate, token=token):
                    try:
                        mapping.remember(
                            token=token,
                            entity_type=entity,
                            normalized_value=normalize_value(value),
                            canonical=value,
                            surrogate=surrogate,
                        )
                    except VaultCollisionError:
                        # Another sidecar instance may have persisted the same token or surrogate
                        # concurrently.
                        concurrent = mapping.surrogate_for_token(token)
                        if concurrent is not None:
                            return token, concurrent
                        continue
                    else:
                        return token, surrogate
        raise VaultCollisionError("Es konnte kein eindeutiger Deckname erzeugt werden.")

    def deanonymize_tokens(self, text: str) -> DeanonymizeResult:
        """Resolve abbreviations in a single, non-recursive pass."""

        if not isinstance(text, str):
            raise TypeError("Rueckholtext muss Text sein.")
        with self._lock:
            mapping = self._existing_mapping_store()
            resolved = 0
            unknown_tokens: set[str] = set()

            def replace(match: re.Match[str]) -> str:
                nonlocal resolved
                token = match.group(0)
                canonical = mapping.resolve_token(token) if mapping is not None else None
                if canonical is None:
                    unknown_tokens.add(token)
                    return token
                resolved += 1
                return canonical

            result = TOKEN_PATTERN.sub(replace, text)
        unknown = sum(
            1 for match in TOKEN_PATTERN.finditer(text) if match.group(0) in unknown_tokens
        )
        return DeanonymizeResult(result, resolved, unknown, len(unknown_tokens))

    def deanonymize_surrogates(self, text: str) -> DeanonymizeResult:
        """Resolve known surrogate names, longest-first and without recursion."""

        if not isinstance(text, str):
            raise TypeError("Rueckholtext muss Text sein.")
        with self._lock:
            mapping = self._existing_mapping_store()
            replacements = mapping.surrogate_mappings() if mapping is not None else ()
        if not replacements:
            return DeanonymizeResult(text, 0, 0, 0)

        by_surrogate = dict(replacements)
        pattern = re.compile(
            r"(?<!\w)(?:"
            + "|".join(re.escape(value) for value in sorted(by_surrogate, key=len, reverse=True))
            + r")(?!\w)"
        )
        resolved = 0

        def replace(match: re.Match[str]) -> str:
            nonlocal resolved
            resolved += 1
            return by_surrogate[match.group(0)]

        return DeanonymizeResult(pattern.sub(replace, text), resolved, 0, 0)

    def status(self) -> VaultStatus:
        metadata = _private_file_metadata(self.secret_path)
        with self._lock:
            mapping = self._existing_mapping_store()
            count, entries = mapping.counts() if mapping is not None else (0, 0)
        return VaultStatus(
            fingerprint=hashlib.sha256(self._secret).hexdigest()[:12],
            created_at=datetime.fromtimestamp(metadata.st_mtime, tz=UTC).isoformat(),
            secret_permissions=(
                f"{stat.S_IMODE(metadata.st_mode):04o}" if os.name == "posix" else "user-private"
            ),
            mapping_persisted=self.mapping_path.exists(),
            mapping_count=count,
            entry_count=entries,
        )

    def rotate_secret(self, *, confirm_loss: bool) -> VaultStatus:
        """Rotate the secret only after explicit loss confirmation."""

        if confirm_loss is not True:
            raise ValueError(ROTATION_WARNING)
        with self._lock:
            if self._mapping is not None:
                self._mapping.close()
                self._mapping = None
            # Explicit confirmation includes loss of old mappings.
            # The path is a fixed direct child of the private state folder.
            self.mapping_path.unlink(missing_ok=True)
            new_secret = os.urandom(SECRET_SIZE)
            _atomic_private_write(self.secret_path, new_secret, replace=True)
            self._secret = _load_secret(self.secret_path)
            return self.status()

    def clear(self, *, confirm: bool) -> VaultStatus:
        """\"Clear table …\": remove all mappings, keep the key.

        Existing codes cannot be restored afterwards; the same name continues to map to the same
        HMAC code.
        """

        if confirm is not True:
            raise ValueError("Leeren nur nach ausdruecklicher Bestaetigung.")
        with self._lock:
            mapping = self._existing_mapping_store()
            if mapping is not None:
                mapping.clear()
            return self.status()

    def purge_unused(self, *, older_than: datetime) -> int:
        """Retention \"Delete unused entries\".

        Deletes entries not saved or used for restoration since ``older_than``. Returns the number
        of mappings.
        """

        if older_than.tzinfo is None:
            raise ValueError("older_than braucht eine Zeitzone.")
        with self._lock:
            mapping = self._existing_mapping_store()
            if mapping is None:
                return 0
            return mapping.purge_unused(older_than.astimezone(UTC).isoformat())

    def export_backup(self, password: str) -> bytes:
        """Table and key as password-protected backup."""

        vault_backup.check_password(password)
        with self._lock:
            mapping = self._existing_mapping_store()
            database = mapping.plain_database() if mapping is not None else _empty_database()
            return vault_backup.seal(self._secret, database, password)

    def import_backup(self, payload: bytes, password: str, *, confirm: bool) -> VaultStatus:
        """Replace this device's key and table with a backup.

        Works like a key change but requires explicit confirmation: the existing
        table entries are removed. The backup is fully validated before any
        replacement occurs.
        """

        if confirm is not True:
            raise ValueError("Import nur nach ausdruecklicher Bestaetigung.")
        content = vault_backup.open_backup(payload, password)
        if len(content.secret) != SECRET_SIZE:
            raise vault_backup.VaultBackupError(
                "invalid_backup", "Der Schluessel in der Sicherung ist ungueltig."
            )
        probe = sqlite3.connect(":memory:")
        try:
            try:
                _adopt_database(probe, content.database)
            except VaultCryptoError as error:
                raise vault_backup.VaultBackupError(
                    "invalid_backup", "Die Tabelle in der Sicherung ist ungueltig."
                ) from error
            probe.commit()
            database = probe.serialize()
        finally:
            probe.close()
        encrypted = _seal_database(
            AESGCM(_derive_key(content.secret, b"presidio-token-mapping-v1")), database
        )
        lock_path = self.mapping_path.with_name(f".{self.mapping_path.name}.lock")
        with self._lock:
            if self._mapping is not None:
                self._mapping.close()
                self._mapping = None
            with _exclusive_file_lock(lock_path):
                _atomic_private_write(self.secret_path, content.secret, replace=True)
                _atomic_private_write(self.mapping_path, encrypted, replace=True)
            self._secret = _load_secret(self.secret_path)
            return self.status()

    def close(self) -> None:
        with self._lock:
            if self._mapping is not None:
                self._mapping.close()
                self._mapping = None

    def _token_digest(self, entity_type: str, normalized_value: str) -> str:
        message = entity_type.encode("ascii") + b"\x00" + normalized_value.encode("utf-8")
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    def _generate_surrogate(
        self, entity_type: str, token: str, attempt: int, language: str = "de"
    ) -> str:
        seed_material = f"{token}\x00{attempt}".encode()
        seed = int.from_bytes(
            hmac.new(self._secret, seed_material, hashlib.sha256).digest()[:8],
            "big",
        )
        # Origin of generated names from the language pack.
        fake = Faker(PACKS.get(language, PACKS["de"]).faker_locale)
        fake.seed_instance(seed)
        if entity_type == "PERSON":
            return f"{fake.first_name()} {fake.last_name()}"
        if entity_type == "ORGANIZATION":
            return fake.company()
        if entity_type in {"LOCATION", "DE_PLZ"}:
            return fake.city()
        if entity_type in {"ADDRESS", "STREET_ADDRESS"}:
            return fake.address().replace("\n", ", ")
        if entity_type == "EMAIL_ADDRESS":
            return fake.ascii_safe_email()
        if entity_type == "PHONE_NUMBER":
            return fake.phone_number()
        return f"{fake.first_name()} {fake.last_name()}"

    def _mapping_store(self) -> _EncryptedSqliteMapping:
        if self._mapping is None:
            self._mapping = _EncryptedSqliteMapping(
                self.mapping_path,
                _derive_key(self._secret, b"presidio-token-mapping-v1"),
                create=True,
            )
        return self._mapping

    def _existing_mapping_store(self) -> _EncryptedSqliteMapping | None:
        if self._mapping is None and self.mapping_path.exists():
            self._mapping = _EncryptedSqliteMapping(
                self.mapping_path,
                _derive_key(self._secret, b"presidio-token-mapping-v1"),
                create=False,
            )
        return self._mapping


class _EncryptedSqliteMapping:
    """In-memory SQLite persisted as a fully encrypted snapshot."""

    def __init__(self, path: Path, key: bytes, *, create: bool) -> None:
        self.path = Path(path)
        self._lock_path = self.path.with_name(f".{self.path.name}.lock")
        self._cipher = AESGCM(key)
        if not hasattr(sqlite3.Connection, "serialize") or not hasattr(
            sqlite3.Connection, "deserialize"
        ):
            raise VaultCryptoError(
                "Diese Python-/SQLite-Laufzeit unterstützt keine sichere Tresor-Serialisierung."
            )
        self._connection = sqlite3.connect(":memory:", check_same_thread=False)
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.RLock()

        if self.path.exists():
            with _exclusive_file_lock(self._lock_path):
                self._load()
        elif create:
            self._create_schema()
        else:
            self._connection.close()
            raise FileNotFoundError(self.path)

    def remember(
        self,
        *,
        token: str,
        entity_type: str,
        normalized_value: str,
        canonical: str,
        surrogate: str | None = None,
    ) -> None:
        with self._lock, _exclusive_file_lock(self._lock_path):
            self._refresh_from_disk()
            snapshot = self._connection.serialize()
            row = self._connection.execute(
                """
                    SELECT entity_type, normalized_value, canonical, canonical_rank, surrogate
                    FROM mappings WHERE token = ?
                    """,
                (token,),
            ).fetchone()
            rank = canonical_rank(canonical)
            changed = False
            if row is None:
                try:
                    self._connection.execute(
                        """
                            INSERT INTO mappings(
                                token, entity_type, normalized_value, canonical,
                                canonical_rank, surrogate, last_used_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                        (
                            token,
                            entity_type,
                            normalized_value,
                            canonical,
                            rank,
                            surrogate,
                            datetime.now(UTC).isoformat(),
                        ),
                    )
                except sqlite3.IntegrityError as error:
                    raise VaultCollisionError("Deckname ist nicht eindeutig.") from error
                changed = True
            else:
                (
                    stored_entity,
                    stored_normalized,
                    stored_canonical,
                    stored_rank,
                    stored_surrogate,
                ) = row
                if stored_entity != entity_type or stored_normalized != normalized_value:
                    raise VaultCollisionError(
                        "Das gekuerzte HMAC-Kuerzel kollidiert mit einem anderen Wert."
                    )
                chosen_canonical = stored_canonical
                chosen_rank = stored_rank
                if rank > stored_rank:
                    chosen_canonical = canonical
                    chosen_rank = rank
                    changed = True
                chosen_surrogate = stored_surrogate
                if surrogate is not None and stored_surrogate is None:
                    chosen_surrogate = surrogate
                    changed = True
                elif (
                    surrogate is not None
                    and stored_surrogate is not None
                    and surrogate != stored_surrogate
                ):
                    raise VaultCollisionError("Ein Kuerzel hat widerspruechliche Decknamen.")
                if changed:
                    try:
                        self._connection.execute(
                            """
                                UPDATE mappings
                                SET canonical = ?, canonical_rank = ?, surrogate = ?
                                WHERE token = ?
                                """,
                            (chosen_canonical, chosen_rank, chosen_surrogate, token),
                        )
                    except sqlite3.IntegrityError as error:
                        raise VaultCollisionError("Deckname ist nicht eindeutig.") from error
            if changed:
                try:
                    self._persist()
                except Exception:
                    # A failed disk write must not leave a mapping that only exists in RAM.
                    self._connection.deserialize(snapshot)
                    raise

    def remember_many(
        self,
        items: tuple[PendingMapping, ...],
        *,
        entry_id: str | None,
        used_at: str,
    ) -> int:
        """All mappings of a commit under a lock and a write operation.

        Each ``remember`` call decrypts and re-encrypts the whole vault.
        An error on the third value would leave the first two unchanged – a half-commit.
        """

        with self._lock, _exclusive_file_lock(self._lock_path):
            self._refresh_from_disk()
            snapshot = self._connection.serialize()
            tokens: set[str] = set()
            try:
                for item in items:
                    self._merge_row(
                        token=item.token,
                        entity_type=item.entity_type,
                        normalized_value=item.normalized_value,
                        canonical=item.canonical,
                        surrogate=item.surrogate,
                        used_at=used_at,
                    )
                    tokens.add(item.token)
                    if entry_id is not None:
                        self._connection.execute(
                            """
                            INSERT INTO usage(token, entry_id, used_at) VALUES (?, ?, ?)
                            ON CONFLICT(token, entry_id) DO UPDATE SET used_at = excluded.used_at
                            """,
                            (item.token, entry_id, used_at),
                        )
                self._persist()
            except Exception:
                self._connection.deserialize(snapshot)
                raise
        return len(tokens)

    def _merge_row(
        self,
        *,
        token: str,
        entity_type: str,
        normalized_value: str,
        canonical: str,
        surrogate: str | None,
        used_at: str,
    ) -> None:
        row = self._connection.execute(
            """
            SELECT entity_type, normalized_value, canonical, canonical_rank, surrogate
            FROM mappings WHERE token = ?
            """,
            (token,),
        ).fetchone()
        rank = canonical_rank(canonical)
        try:
            if row is None:
                self._connection.execute(
                    """
                    INSERT INTO mappings(
                        token, entity_type, normalized_value, canonical,
                        canonical_rank, surrogate, last_used_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (token, entity_type, normalized_value, canonical, rank, surrogate, used_at),
                )
                return
            stored_entity, stored_normalized, stored_canonical, stored_rank, stored_surrogate = row
            if stored_entity != entity_type or stored_normalized != normalized_value:
                raise VaultCollisionError(
                    "Das gekuerzte HMAC-Kuerzel kollidiert mit einem anderen Wert."
                )
            if (
                surrogate is not None
                and stored_surrogate is not None
                and surrogate != stored_surrogate
            ):
                raise VaultCollisionError("Ein Kuerzel hat widerspruechliche Decknamen.")
            chosen_canonical, chosen_rank = (
                (canonical, rank) if rank > stored_rank else (stored_canonical, stored_rank)
            )
            self._connection.execute(
                """
                UPDATE mappings SET canonical = ?, canonical_rank = ?, surrogate = ?,
                    last_used_at = max(coalesce(last_used_at, ''), ?)
                WHERE token = ?
                """,
                (chosen_canonical, chosen_rank, stored_surrogate or surrogate, used_at, token),
            )
        except sqlite3.IntegrityError as error:
            raise VaultCollisionError("Deckname ist nicht eindeutig.") from error

    def lookup(self, tokens: set[str] | frozenset[str]) -> dict[str, TokenRecord]:
        wanted = sorted(tokens)
        with self._lock, _exclusive_file_lock(self._lock_path):
            self._refresh_from_disk()
            rows: list[tuple[str, str, str]] = []
            usage: list[tuple[str, str, str]] = []
            # SQLite limits the number of parameters per query.
            for start in range(0, len(wanted), 500):
                chunk = wanted[start : start + 500]
                marks = ",".join("?" * len(chunk))
                rows.extend(
                    self._connection.execute(
                        "SELECT token, entity_type, canonical FROM mappings "
                        f"WHERE token IN ({marks})",
                        chunk,
                    ).fetchall()
                )
                usage.extend(
                    self._connection.execute(
                        "SELECT token, entry_id, used_at FROM usage "
                        f"WHERE token IN ({marks}) ORDER BY used_at, entry_id",
                        chunk,
                    ).fetchall()
                )
        origins: dict[str, list[tuple[str, str]]] = {}
        for token, entry_id, used_at in usage:
            origins.setdefault(str(token), []).append((str(entry_id), str(used_at)))
        return {
            str(token): TokenRecord(
                entity_type=str(entity_type),
                canonical=str(canonical),
                origins=tuple(origins.get(str(token), ())),
            )
            for token, entity_type, canonical in rows
        }

    def surrogate_tokens(self) -> tuple[tuple[str, str], ...]:
        with self._lock, _exclusive_file_lock(self._lock_path):
            self._refresh_from_disk()
            rows = self._connection.execute(
                """
                SELECT surrogate, token FROM mappings
                WHERE surrogate IS NOT NULL ORDER BY length(surrogate) DESC, surrogate
                """
            ).fetchall()
        return tuple((str(surrogate), str(token)) for surrogate, token in rows)

    def touch(self, tokens: set[str] | frozenset[str], *, used_at: str) -> int:
        wanted = sorted(tokens)
        with self._lock, _exclusive_file_lock(self._lock_path):
            self._refresh_from_disk()
            snapshot = self._connection.serialize()
            known = 0
            try:
                for start in range(0, len(wanted), 500):
                    chunk = wanted[start : start + 500]
                    marks = ",".join("?" * len(chunk))
                    cursor = self._connection.execute(
                        f"UPDATE mappings SET last_used_at = ? WHERE token IN ({marks})",
                        (used_at, *chunk),
                    )
                    known += max(cursor.rowcount, 0)
                if known:
                    self._persist()
            except Exception:
                self._connection.deserialize(snapshot)
                raise
        return known

    def last_used(self, token: str) -> str | None:
        with self._lock, _exclusive_file_lock(self._lock_path):
            self._refresh_from_disk()
            row = self._connection.execute(
                "SELECT last_used_at FROM mappings WHERE token = ?",
                (token,),
            ).fetchone()
        return str(row[0]) if row is not None and row[0] is not None else None

    def usage_for_token(self, token: str) -> tuple[tuple[str, str], ...]:
        with self._lock, _exclusive_file_lock(self._lock_path):
            self._refresh_from_disk()
            rows = self._connection.execute(
                "SELECT entry_id, used_at FROM usage WHERE token = ? ORDER BY used_at, entry_id",
                (token,),
            ).fetchall()
        return tuple((str(entry_id), str(used_at)) for entry_id, used_at in rows)

    def resolve_token(self, token: str) -> str | None:
        with self._lock, _exclusive_file_lock(self._lock_path):
            self._refresh_from_disk()
            row = self._connection.execute(
                "SELECT canonical FROM mappings WHERE token = ?",
                (token,),
            ).fetchone()
        return str(row[0]) if row is not None else None

    def surrogate_for_token(self, token: str) -> str | None:
        with self._lock, _exclusive_file_lock(self._lock_path):
            self._refresh_from_disk()
            row = self._connection.execute(
                "SELECT surrogate FROM mappings WHERE token = ?",
                (token,),
            ).fetchone()
        if row is None or row[0] is None:
            return None
        return str(row[0])

    def surrogate_available(self, surrogate: str, *, token: str) -> bool:
        with self._lock, _exclusive_file_lock(self._lock_path):
            self._refresh_from_disk()
            row = self._connection.execute(
                "SELECT token FROM mappings WHERE surrogate = ?",
                (surrogate,),
            ).fetchone()
        return row is None or row[0] == token

    def surrogate_mappings(self) -> tuple[tuple[str, str], ...]:
        with self._lock, _exclusive_file_lock(self._lock_path):
            self._refresh_from_disk()
            rows = self._connection.execute(
                """
                    SELECT surrogate, canonical FROM mappings
                    WHERE surrogate IS NOT NULL ORDER BY length(surrogate) DESC, surrogate
                    """
            ).fetchall()
        return tuple((str(surrogate), str(canonical)) for surrogate, canonical in rows)

    def count(self) -> int:
        with self._lock, _exclusive_file_lock(self._lock_path):
            self._refresh_from_disk()
            row = self._connection.execute("SELECT count(*) FROM mappings").fetchone()
        return int(row[0])

    def counts(self) -> tuple[int, int]:
        """(mappings, entries with mappings)."""

        with self._lock, _exclusive_file_lock(self._lock_path):
            self._refresh_from_disk()
            mappings = self._connection.execute("SELECT count(*) FROM mappings").fetchone()
            entries = self._connection.execute(
                "SELECT count(DISTINCT entry_id) FROM usage"
            ).fetchone()
        return int(mappings[0]), int(entries[0])

    def clear(self) -> int:
        return self._delete("DELETE FROM mappings", ())

    def purge_unused(self, cutoff: str) -> int:
        return self._delete(
            "DELETE FROM mappings WHERE coalesce(last_used_at, '') < ?",
            (cutoff,),
        )

    def plain_database(self) -> bytes:
        """Serialized SQLite DB (plain text, for backup only)."""

        with self._lock, _exclusive_file_lock(self._lock_path):
            self._refresh_from_disk()
            self._connection.commit()
            return self._connection.serialize()

    def _delete(self, statement: str, parameters: tuple[str, ...]) -> int:
        with self._lock, _exclusive_file_lock(self._lock_path):
            self._refresh_from_disk()
            snapshot = self._connection.serialize()
            try:
                cursor = self._connection.execute(statement, parameters)
                removed = max(cursor.rowcount, 0)
                # Delete usage rows that reference removed mappings;
                # also clean up orphaned rows from older states.
                self._connection.execute(
                    "DELETE FROM usage WHERE token NOT IN (SELECT token FROM mappings)"
                )
                if removed:
                    self._persist()
                else:
                    self._connection.commit()
            except Exception:
                self._connection.deserialize(snapshot)
                raise
        return removed

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _create_schema(self) -> None:
        with self._connection:
            self._connection.executescript(_EMPTY_SCHEMA)
            self._connection.executescript(_USAGE_SCHEMA)

    def _load(self) -> None:
        payload = _read_private_file(self.path)
        minimum_size = len(DATABASE_HEADER) + NONCE_SIZE + 16
        if len(payload) < minimum_size or not payload.startswith(DATABASE_HEADER):
            raise VaultCryptoError("Unbekanntes oder unvollstaendiges Tresorformat.")
        nonce_start = len(DATABASE_HEADER)
        nonce_end = nonce_start + NONCE_SIZE
        try:
            serialized = self._cipher.decrypt(
                payload[nonce_start:nonce_end],
                payload[nonce_end:],
                DATABASE_HEADER,
            )
        except InvalidTag as error:
            raise VaultCryptoError(
                "Tresor-Zuordnung konnte nicht authentifiziert werden."
            ) from error
        _adopt_database(self._connection, serialized)

    def _refresh_from_disk(self) -> None:
        if self.path.exists():
            self._load()

    def _persist(self) -> None:
        self._connection.commit()
        payload = _seal_database(self._cipher, self._connection.serialize())
        _atomic_private_write(self.path, payload, replace=True)


def _seal_database(cipher: AESGCM, serialized: bytes) -> bytes:
    nonce = os.urandom(NONCE_SIZE)
    return DATABASE_HEADER + nonce + cipher.encrypt(nonce, serialized, DATABASE_HEADER)


def _adopt_database(connection: sqlite3.Connection, serialized: bytes) -> None:
    """Adopt a decrypted table and upgrade it to the current schema."""

    try:
        connection.deserialize(serialized)
        row = connection.execute("SELECT schema_version FROM vault_metadata").fetchone()
    except sqlite3.DatabaseError as error:
        raise VaultCryptoError("Entschluesselte Tresor-Zuordnung ist ungueltig.") from error
    if row is not None and row[0] in {1, 2}:
        # Schema 1 lacked an origin per token, schema 2 lacked a last-used timestamp.
        # Both fields are added in memory and persisted on the next write; existing
        # mappings stay valid. The last-used timestamp starts at the last save—or
        # at load time if no prior value exists.
        with connection:
            connection.executescript(_USAGE_SCHEMA)
            columns = {str(item[1]) for item in connection.execute("PRAGMA table_info(mappings)")}
            if "last_used_at" not in columns:
                connection.execute("ALTER TABLE mappings ADD COLUMN last_used_at TEXT")
            connection.execute(
                """
                UPDATE mappings SET last_used_at = coalesce(
                    (SELECT max(used_at) FROM usage WHERE usage.token = mappings.token), ?
                ) WHERE last_used_at IS NULL
                """,
                (datetime.now(UTC).isoformat(),),
            )
            connection.execute(
                "UPDATE vault_metadata SET schema_version = ?", (DATABASE_SCHEMA_VERSION,)
            )
        return
    if row is None or row[0] != DATABASE_SCHEMA_VERSION:
        raise VaultCryptoError("Nicht unterstuetzte Tresor-Schemaversion.")
    try:
        connection.execute("SELECT token, entity_type, canonical FROM mappings LIMIT 1")
        connection.execute("SELECT token, entry_id, used_at FROM usage LIMIT 1")
    except sqlite3.DatabaseError as error:
        raise VaultCryptoError("Entschluesselte Tresor-Zuordnung ist unvollstaendig.") from error


def _empty_database() -> bytes:
    connection = sqlite3.connect(":memory:")
    try:
        with connection:
            connection.executescript(_EMPTY_SCHEMA)
            connection.executescript(_USAGE_SCHEMA)
        return connection.serialize()
    finally:
        connection.close()


_EMPTY_SCHEMA: Final = """
CREATE TABLE vault_metadata (
    schema_version INTEGER NOT NULL
);
INSERT INTO vault_metadata(schema_version) VALUES (3);
CREATE TABLE mappings (
    token TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    normalized_value TEXT NOT NULL,
    canonical TEXT NOT NULL,
    canonical_rank INTEGER NOT NULL,
    surrogate TEXT UNIQUE,
    last_used_at TEXT
);
"""

_USAGE_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS usage (
    token TEXT NOT NULL REFERENCES mappings(token) ON DELETE CASCADE,
    entry_id TEXT NOT NULL,
    used_at TEXT NOT NULL,
    PRIMARY KEY (token, entry_id)
);
"""


def _with_surrogate(pending: PendingMapping, surrogate: str) -> PendingMapping:
    return PendingMapping(
        token=pending.token,
        entity_type=pending.entity_type,
        normalized_value=pending.normalized_value,
        canonical=pending.canonical,
        surrogate=surrogate,
    )


def _derive_key(secret: bytes, purpose: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=purpose,
    ).derive(secret)


def _prepare_private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise VaultCryptoError("Tresor-State muss ein regulaeres Verzeichnis sein.")
    if os.name == "posix":
        path.chmod(0o700)
    elif os.name == "nt":
        _secure_windows_path(path, directory=True)


def _reject_project_state_directory(path: Path) -> None:
    """Prevent storing secrets inside a recognizable project tree."""

    candidate = path.expanduser().resolve(strict=False)
    for parent in (candidate, *candidate.parents):
        if (parent / ".git").exists() and (
            (parent / "pyproject.toml").exists()
            or (parent / "package.json").exists()
            or (parent / "sidecar" / "pyproject.toml").exists()
        ):
            raise VaultCryptoError(
                "Der Token-Tresor darf nicht im Projekt- oder Quellordner liegen."
            )


def _load_or_create_secret(path: Path) -> bytes:
    try:
        return _load_secret(path)
    except FileNotFoundError:
        pass
    secret = os.urandom(SECRET_SIZE)
    try:
        _atomic_private_write(path, secret, replace=False)
    except FileExistsError:
        return _load_secret(path)
    return _load_secret(path)


def _load_secret(path: Path) -> bytes:
    secret = _read_private_file(path, max_bytes=SECRET_SIZE + 1)
    if len(secret) != SECRET_SIZE:
        raise VaultCryptoError(f"Tresor-Secret muss genau {SECRET_SIZE} Bytes enthalten.")
    return secret


def _read_private_file(path: Path, *, max_bytes: int | None = None) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError as error:
        raise VaultCryptoError(
            "Private Tresordatei konnte nicht sicher geoeffnet werden."
        ) from error
    try:
        metadata = os.fstat(descriptor)
        _validate_private_metadata(path, metadata)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            return stream.read(max_bytes)
    except OSError as error:
        raise VaultCryptoError("Private Tresordatei konnte nicht gelesen werden.") from error
    finally:
        os.close(descriptor)


def _private_file_metadata(path: Path) -> os.stat_result:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise VaultCryptoError("Private Tresordatei konnte nicht geoeffnet werden.") from error
    try:
        metadata = os.fstat(descriptor)
        _validate_private_metadata(path, metadata)
        return metadata
    finally:
        os.close(descriptor)


def _validate_private_metadata(path: Path, metadata: os.stat_result) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise VaultCryptoError(f"{path.name} ist keine regulaere Datei.")
    if os.name == "posix":
        mode = stat.S_IMODE(metadata.st_mode)
        if mode != 0o600:
            raise VaultCryptoError(f"{path.name} braucht POSIX-Rechte 0600, hat aber {mode:04o}.")
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            raise VaultCryptoError(f"{path.name} gehoert nicht dem aktuellen Benutzer.")
    elif os.name == "nt":
        _secure_windows_path(path, directory=False)


@contextmanager
def _exclusive_file_lock(path: Path):
    """Cross-process lock for merge and atomic DB swap."""

    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise VaultCryptoError("Tresor-Sperrdatei konnte nicht geoeffnet werden.") from error
    locked = False
    try:
        metadata = os.fstat(descriptor)
        _validate_private_metadata(path, metadata)
        if metadata.st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        if os.name == "nt":
            import msvcrt

            _secure_windows_path(path, directory=False)
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        yield
    finally:
        if locked:
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _secure_windows_path(path: Path, *, directory: bool) -> None:
    """Set a non-inheritable ACL for the current user on Windows."""

    if os.name != "nt":
        return
    try:
        identity = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        # Output: "DOMAIN\\user","S-1-5-..." — the SID is language-neutral.
        match = re.search(r'"(S-\d+(?:-\d+)+)"', identity)
        if match is None:
            raise ValueError("SID fehlt")
        permission = "(OI)(CI)F" if directory else "F"
        subprocess.run(
            [
                "icacls",
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"*{match.group(1)}:{permission}",
                "/q",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise VaultCryptoError(
            "Private Windows-Dateirechte konnten nicht gesetzt oder geprüft werden."
        ) from error


def _atomic_private_write(path: Path, payload: bytes, *, replace: bool) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            if os.name == "nt":
                _secure_windows_path(temporary, directory=False)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
            temporary = None
        else:
            os.link(temporary, path, follow_symlinks=False)
        if os.name == "posix":
            path.chmod(0o600)
        elif os.name == "nt":
            _secure_windows_path(path, directory=False)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
