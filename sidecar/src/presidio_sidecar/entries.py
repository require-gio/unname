"""Encrypted entry store for the history.

An *entry* groups files (or a text) that share a masking. The store keeps only what the
history needs: title, timestamp, kind, status, file list with counters, the
content-free review state (finding IDs, offsets, decisions), the draft state and
the export history (time, method, folder, filenames, check data). Document text,
original values and replacements are never stored here – they live in the draft
state (``drafts.py``), encrypted with a separate key per entry.

This key (wrapped with the main draft-state key) is kept by the history alongside
the entry, not inside the entry: it never leaves the sidecar via the API, and
deleting an entry removes both entry and key atomically (crypto-shredding). Older
entries without a draft-state key are re-loaded as new (see spec §9).

Filenames may still contain personal names (e.g. “Angebot_Hofer.docx”). Therefore
the store, like the master-data registry, is stored on disk as an AES-256-GCM
ciphertext with its own key (0600) and a custom AAD marker – a registry
ciphertext cannot be mistaken for a history file and vice-versa.

Deletion removes the entry, file list and draft state; the translation table
remains (its own retention period)."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading
from collections.abc import Callable, Collection
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .masking import MaskingSpec, ResultLabel
from .registry_crypto import load_or_create_registry_key

FORMAT_HEADER: Final = b"UNNAME-ENTRIES\x01"
#: 2: Wrapped keys of the draft state per entry.
#: 3: Draft state, export history, and confirmation per masking.
#: 4: Immutable short reference per entry (``display_ref``, history H1).
#: Forward migration: Versions knowing only 1–3 do not read schema 4.
STORE_SCHEMA_VERSION: Final = 4
NONCE_SIZE: Final = 12

#: Short references ("#K7M2QX") from an alphabet without confusables (0/O, 1/I/L).
REFERENCE_ALPHABET: Final = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
REFERENCE_LENGTH: Final = 6
#: As often a new roll is done per length before the reference becomes longer.
REFERENCE_ATTEMPTS: Final = 32
REFERENCE_PATTERN: Final = rf"^[{REFERENCE_ALPHABET}]{{{REFERENCE_LENGTH},16}}$"

EntryKind = Literal["documents", "text"]
EntryStatus = Literal["processing", "review", "saved", "copied", "failed"]
#: ``expired``: Old entry without a draft state, job data is missing.
#: ``unavailable``: Draft state unreadable (missing, tampered, unknown schema) – fallback is
#: “select original”.
FileStatus = Literal[
    "queued", "processing", "review", "failed", "cancelled", "expired", "unavailable"
]
ResidualStatus = Literal["clear", "warning", "not_run", "failed"]


class EntryError(RuntimeError):
    """The entry store could not safely perform an operation."""


class EntryNotFoundError(EntryError, LookupError):
    pass


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FileOutput(_Model):
    """Data stored when saving a file – counters and check info only.

    Basis for the “Saved” view and the check report (spec §5). No value,
    replacement, or text excerpt.
    """

    #: ``saved``: Protected copy exists in the target folder. ``unchanged``: nothing
    #: needed masking; an unverified copy (with metadata like the author) is
    #: intentionally not written.
    status: Literal["saved", "unchanged"]
    #: Filename of the protected copy, e.g., ``Rechnung_geschützt.pdf``.
    name: str | None = None
    format: str | None = None
    sha256: str | None = None
    size_bytes: int = Field(default=0, ge=0)
    #: A picture or scan was saved as text: "Text from the image".
    from_scan: bool = False
    #: … because the OCR could not read back a drawn-in code.
    readback_failed: bool = False
    #: Scan-PDF, saved as PDF: overpainted, codes as text, invisible text layer from the
    #: OCR.
    scan_text_layer: bool = False
    label: ResultLabel | None = None
    contains_surrogates: bool = False
    protection_level: int = Field(default=1, ge=1, le=3)
    masked: int = Field(default=0, ge=0)
    masked_by_category: dict[str, int] = Field(default_factory=dict)
    masked_by_entity: dict[str, int] = Field(default_factory=dict)
    #: Decisions by source (spec §4): user, AI suggestion and open questions that
    #: replaced “mask if in doubt”.
    decided_by_user: int = Field(default=0, ge=0)
    decided_by_ai: int = Field(default=0, ge=0)
    masked_without_decision: int = Field(default=0, ge=0)
    kept_here: int = Field(default=0, ge=0)
    added_by_hand: int = Field(default=0, ge=0)
    adjusted: int = Field(default=0, ge=0)
    residual_risk: ResidualStatus = "not_run"
    residual_classes: tuple[str, ...] = ()
    anonymous_blockers: tuple[str, ...] = ()
    rendered_pages: int = Field(default=0, ge=0)
    text_extraction_checked: bool = False
    verified_targets: int = Field(default=0, ge=0)
    audit_id: int | None = None


class EntryFile(_Model):
    #: The file ID equals the detection job ID, remaining stable for the file's
    #: lifetime: After a restart, the sidecar loads the job from the draft.
    id: str
    name: str
    format: str
    size_bytes: int = Field(ge=0)
    pages: int | None = None
    ocr_used: bool = False
    status: FileStatus = "queued"
    error_code: str | None = None
    #: Hits per category (counting tool, no content).
    counts: dict[str, int] = Field(default_factory=dict)
    label: ResultLabel | None = None
    #: Where the protected copy is located; set only after the harness publishes it
    #: (``POST /entries/{id}/outputs``).
    output_path: str | None = None
    #: SHA-256 of the uploaded bytes. The shell uses this to match files to their
    #: originals, and “Nochmal bearbeiten” detects a modified file.
    source_sha256: str | None = None
    #: The original was re-read and has changed since the first read – saved decisions
    #: apply only where the locations remain the same.
    source_changed: bool = False
    #: “Neu erkennen” re-reads the file; the text differs, so decisions and manual
    #: entries are kept only where the text is unchanged.
    extraction_changed: bool = False
    output: FileOutput | None = None


class DecisionState(_Model):
    finding_id: str
    action: Literal["accept", "reject"]
    by: Literal["user", "ai-suggestion"] = "user"


class ManualState(_Model):
    entity_type: str
    #: UTF-16 offsets in the file's extracted text.
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    corrects_finding_id: str | None = None


class FileReview(_Model):
    decisions: tuple[DecisionState, ...] = ()
    manual: tuple[ManualState, ...] = ()


class ExportedFile(_Model):
    """A file from a storage in the export history – like ``FileOutput``, but content-free."""

    file_id: str
    #: Name of the original at the time of storage.
    name: str
    format: str
    #: Where the harness published the copy; ``None`` if no copy exists.
    path: str | None = None
    output: FileOutput


class EntryExport(_Model):
    """A storage in the export history.

    Time, masking (method, style, categories), target folder, filenames,
    labels, and review data – never a value. The last storage is additionally
    stored as before in ``Entry.saved_at``, ``output_dir`` and
    ``EntryFile.output``.
    """

    id: str
    saved_at: datetime
    masking: MaskingSpec
    directory: str
    #: Draft state that was saved (``Entry.draft_revision``).
    revision: int = Field(default=0, ge=0)
    files: tuple[ExportedFile, ...] = ()


#: An entry tracks this many saves; older ones drop from the history (files in the
#: target folder remain). Keeps ``entries.enc`` small, as it is rewritten on every
#: decision.
MAX_EXPORTS: Final = 25


def confirmation_key(masking: MaskingSpec) -> str:
    """What a confirmation from spec §4 applies to: method and style."""

    return f"{masking.method}:{masking.style}:{'anonymous' if masking.anonymize else '-'}"


class Entry(_Model):
    id: str
    #: Short, immutable reference for finding in the history (e.g. “K7M2QX”). Random,
    #: never derived from content or name; unique only among this device’s entries and
    #: no substitute for ``id``. ``None`` only when reading legacy data—the storage
    #: assigns it before delivering.
    display_ref: str | None = Field(default=None, pattern=REFERENCE_PATTERN)
    title: str = Field(max_length=200)
    #: ``True`` once someone has renamed it; then the sidecar no longer changes the
    #: title itself.
    title_custom: bool = False
    kind: EntryKind
    status: EntryStatus = "processing"
    created_at: datetime
    updated_at: datetime
    masking: MaskingSpec
    files: tuple[EntryFile, ...] = ()
    #: Review state per file ID so that “Nochmal bearbeiten” can reapply the decisions.
    review: dict[str, FileReview] = Field(default_factory=dict)
    #: Last saved (documents) — only after the harness has published the files.
    saved_at: datetime | None = None
    #: Target folder of the last save, for “in … · Originale unveraendert” and “Ordner oeffnen”.
    output_dir: str | None = None
    #: Irreversible save is confirmed for the current masking (E4). Derived from
    #: ``irreversible_confirmed_for``: switching method or style requires reconfirmation
    #: on the next save.
    irreversible_confirmed: bool = False
    #: Which masking the confirmation applies to (``confirmation_key``).
    irreversible_confirmed_for: str | None = None
    #: How often an answer with codes from this entry was copied or saved (“3 Dateien ·
    #: 1 Rückübersetzung”). Only the number, never the answer.
    restores: int = Field(default=0, ge=0)
    restored_at: datetime | None = None
    #: Increments on every change to masking, review, file list, or detection; the
    #: storage counts itself.
    draft_revision: int = Field(default=0, ge=0)
    #: Which draft state was last saved; ``None``: never.
    saved_revision: int | None = Field(default=None, ge=0)
    #: Export history, oldest save first.
    exports: tuple[EntryExport, ...] = Field(default=(), max_length=MAX_EXPORTS)

    @model_validator(mode="before")
    @classmethod
    def _upgrade(cls, data: Any) -> Any:
        # Legacy data from before AS3: a confirmed save applied to the then-current
        # masking, and what was saved is considered unchanged.
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if data.get("irreversible_confirmed") and data.get("irreversible_confirmed_for") is None:
            masking = data.get("masking")
            spec = masking if isinstance(masking, MaskingSpec) else None
            if spec is None and isinstance(masking, dict):
                with suppress(ValidationError):
                    spec = MaskingSpec.model_validate(masking)
            if spec is not None:
                data["irreversible_confirmed_for"] = confirmation_key(spec)
        if data.get("saved_at") is not None and data.get("saved_revision") is None:
            data["saved_revision"] = data.get("draft_revision") or 0
        return data

    def file(self, file_id: str) -> EntryFile | None:
        return next((item for item in self.files if item.id == file_id), None)

    @property
    def changed_since_save(self) -> bool:
        return self.saved_revision is not None and self.draft_revision > self.saved_revision


def _draft_content(entry: Entry) -> tuple[Any, ...]:
    """What constitutes a draft: masking, per-file review, and the file list.

    Order and empty review states do not matter – opening a saved file only changes
    nothing.
    """

    review = {
        file_id: (frozenset(value.decisions), frozenset(value.manual))
        for file_id, value in entry.review.items()
        if value.decisions or value.manual
    }
    return entry.masking, review, tuple(item.id for item in entry.files)


def _with_invariants(current: Entry, updated: Entry) -> Entry:
    """Revision and confirmation follow the draft – equally for every write path."""

    changes: dict[str, Any] = {}
    if _draft_content(updated) != _draft_content(current) and (
        updated.draft_revision == current.draft_revision
    ):
        changes["draft_revision"] = current.draft_revision + 1
    confirmed = updated.irreversible_confirmed_for == confirmation_key(updated.masking)
    if updated.irreversible_confirmed != confirmed:
        changes["irreversible_confirmed"] = confirmed
    return updated.model_copy(update=changes) if changes else updated


def _random_reference(length: int) -> str:
    return "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(length))


def allocate_reference(
    taken: Collection[str],
    *,
    generate: Callable[[int], str] = _random_reference,
) -> str:
    """A reference that is still free under ``taken``.

    Collisions are re-randomized; after ``REFERENCE_ATTEMPTS`` attempts the reference
    becomes one character longer, ensuring allocation always terminates.
    """

    length = REFERENCE_LENGTH
    while True:
        for _ in range(REFERENCE_ATTEMPTS):
            candidate = generate(length)
            if candidate not in taken:
                return candidate
        length += 1
        if length > 16:
            raise EntryError("Keine freie Kurzreferenz gefunden.")


class _StoreDocument(_Model):
    schema_version: Literal[1, 2, 3, 4] = STORE_SCHEMA_VERSION
    entries: tuple[Entry, ...] = ()
    #: Wrapped draft key per entry ID (from schema 2).
    draft_keys: dict[str, str] = Field(default_factory=dict)


class EntryStore:
    """Thread-safe storage; every change is written atomically encrypted."""

    def __init__(
        self,
        path: Path,
        key_path: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path)
        self._cipher = AESGCM(load_or_create_registry_key(Path(key_path)))
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        #: Replaceable for tests that force collisions.
        self._reference_generator: Callable[[int], str] = _random_reference
        self._draft_keys: dict[str, str] = {}
        self._entries: dict[str, Entry] = self._load()

    # -- Reading ---------------------------------------------------------------

    def list(self) -> list[Entry]:
        with self._lock:
            return sorted(
                self._entries.values(),
                key=lambda entry: (entry.created_at, entry.id),
                reverse=True,
            )

    def get(self, entry_id: str) -> Entry:
        with self._lock:
            try:
                return self._entries[entry_id]
            except KeyError as error:
                raise EntryNotFoundError("Eintrag nicht gefunden.") from error

    def find_by_file(self, file_id: str) -> Entry | None:
        with self._lock:
            return next(
                (entry for entry in self._entries.values() if entry.file(file_id) is not None),
                None,
            )

    def draft_key(self, entry_id: str) -> str | None:
        """Wrapped key of the draft; ``None`` for legacy data."""

        with self._lock:
            return self._draft_keys.get(entry_id)

    # -- Writing -----------------------------------------------------------

    def create(
        self,
        *,
        kind: EntryKind,
        masking: MaskingSpec,
        title: str,
        title_custom: bool = False,
        status: EntryStatus = "processing",
        draft_key: Callable[[str], str] | None = None,
    ) -> Entry:
        """Legt einen Eintrag an; ``draft_key`` erzeugt seinen Arbeitsstands-Schluessel."""

        now = self._clock()
        entry_id = secrets.token_hex(12)
        key = draft_key(entry_id) if draft_key is not None else None
        with self._lock:
            # Allocated under the lock: concurrent creation sees the other's references
            # and never picks the same one.
            entry = Entry(
                id=entry_id,
                display_ref=self._allocate_reference(self._entries),
                title=title,
                title_custom=title_custom,
                kind=kind,
                status=status,
                created_at=now,
                updated_at=now,
                masking=masking,
            )
            entries = {**self._entries, entry.id: entry}
            keys = {**self._draft_keys, entry.id: key} if key is not None else self._draft_keys
            self._persist(entries, keys)
            self._entries = entries
            self._draft_keys = keys
        return entry

    def ensure_draft_key(self, entry_id: str, create: Callable[[str], str]) -> str:
        """Draft key; legacy data gets it on first read."""

        with self._lock:
            self.get(entry_id)
            existing = self._draft_keys.get(entry_id)
            if existing is not None:
                return existing
            key = create(entry_id)
            keys = {**self._draft_keys, entry_id: key}
            self._persist(self._entries, keys)
            self._draft_keys = keys
            return key

    def update(self, entry_id: str, change: Callable[[Entry], Entry]) -> Entry:
        """Changes exactly one entry; ``change`` runs under the lock."""

        with self._lock:
            current = self.get(entry_id)
            updated = change(current)
            if updated == current:
                return current
            if updated.id != current.id:
                raise EntryError("Die ID eines Eintrags ist unveraenderlich.")
            if updated.display_ref != current.display_ref:
                raise EntryError("Die Kurzreferenz eines Eintrags ist unveraenderlich.")
            updated = _with_invariants(current, updated)
            updated = updated.model_copy(update={"updated_at": self._clock()})
            # Re-validate: ``model_copy`` does not check.
            updated = Entry.model_validate(updated.model_dump())
            entries = {**self._entries, entry_id: updated}
            self._persist(entries, self._draft_keys)
            self._entries = entries
            return updated

    def delete(self, entry_id: str) -> Entry:
        with self._lock:
            entry = self.get(entry_id)
            self._remove({entry_id})
            return entry

    def delete_many(self, entry_ids: Collection[str]) -> list[Entry]:
        """Deletes the mentioned entries in a write operation; unknown ones are missing."""

        with self._lock:
            removed = [self._entries[key] for key in entry_ids if key in self._entries]
            if removed:
                self._remove({entry.id for entry in removed})
            return removed

    def clear(self) -> list[Entry]:
        with self._lock:
            removed = list(self._entries.values())
            self._remove(set(self._entries))
            return removed

    def expired(self, retention_days: int | None, *, now: datetime | None = None) -> list[Entry]:
        """Entries that have been unchanged for longer than allowed – without deleting them."""

        if retention_days is None:
            return []
        cutoff = (now or self._clock()) - timedelta(days=retention_days)
        with self._lock:
            return [entry for entry in self._entries.values() if entry.updated_at < cutoff]

    def purge_expired(
        self,
        retention_days: int | None,
        *,
        now: datetime | None = None,
        only: Collection[str] | None = None,
    ) -> list[Entry]:
        """Fristen-Janitor: deletes entries that have been unchanged for longer than
        allowed.
        allowed.

        ``only`` restricts to previously locked candidates: anyone who has
        changed since then remains.
        """

        with self._lock:
            expired = self.expired(retention_days, now=now)
            if only is not None:
                allowed = set(only)
                expired = [entry for entry in expired if entry.id in allowed]
            if expired:
                self._remove({entry.id for entry in expired})
            return expired

    def _allocate_reference(self, entries: dict[str, Entry]) -> str:
        taken = {entry.display_ref for entry in entries.values() if entry.display_ref}
        return allocate_reference(taken, generate=self._reference_generator)

    def _remove(self, entry_ids: set[str]) -> None:
        # Remove the entry and its draft key together.
        entries = {key: value for key, value in self._entries.items() if key not in entry_ids}
        keys = {key: value for key, value in self._draft_keys.items() if key not in entry_ids}
        self._persist(entries, keys)
        self._entries = entries
        self._draft_keys = keys

    # -- Disk --------------------------------------------------------------

    def _load(self) -> dict[str, Entry]:
        try:
            payload = _read_private(self.path)
        except FileNotFoundError:
            return {}
        minimum = len(FORMAT_HEADER) + NONCE_SIZE + 16
        if len(payload) < minimum or not payload.startswith(FORMAT_HEADER):
            raise EntryError("Unbekanntes oder unvollstaendiges Verlaufsformat.")
        nonce_end = len(FORMAT_HEADER) + NONCE_SIZE
        try:
            plaintext = self._cipher.decrypt(
                payload[len(FORMAT_HEADER) : nonce_end],
                payload[nonce_end:],
                FORMAT_HEADER,
            )
            document = _StoreDocument.model_validate(json.loads(plaintext))
        except InvalidTag as error:
            raise EntryError("Der Verlauf konnte nicht authentifiziert werden.") from error
        except (UnicodeError, json.JSONDecodeError, ValidationError) as error:
            raise EntryError("Der entschluesselte Verlauf ist ungueltig.") from error
        entries = {entry.id: entry for entry in document.entries}
        keys = {key: value for key, value in document.draft_keys.items() if key in entries}
        upgraded = _with_references(entries, self._reference_generator)
        if document.schema_version != STORE_SCHEMA_VERSION or upgraded != entries:
            # Once and before delivery: Only when the new state is atomically
            # on disk do the references exist. If writing fails, the old file
            # remains, and the history reports the error instead of appearing
            # empty or with fleeting references.
            self._persist(upgraded, keys)
        self._draft_keys = keys
        return upgraded

    def _persist(self, entries: dict[str, Entry], keys: dict[str, str]) -> None:
        document = _StoreDocument(
            entries=tuple(entries.values()),
            # Never a key without an entry: Deletion is a write operation.
            draft_keys={key: value for key, value in keys.items() if key in entries},
        )
        plaintext = document.model_dump_json().encode("utf-8")
        nonce = os.urandom(NONCE_SIZE)
        payload = FORMAT_HEADER + nonce + self._cipher.encrypt(nonce, plaintext, FORMAT_HEADER)
        _atomic_private_write(self.path, payload)


def _with_references(entries: dict[str, Entry], generate: Callable[[int], str]) -> dict[str, Entry]:
    """Assign missing (or duplicate) references once, oldest first.

    Deliberately not via ``update``: timestamps, revisions, exports and thus
    the deletion deadlines remain untouched. Existing valid references
    remain as they are.
    """

    ordered = sorted(entries.values(), key=lambda entry: (entry.created_at, entry.id))
    taken: set[str] = set()
    missing: list[Entry] = []
    for entry in ordered:
        if entry.display_ref is None or entry.display_ref in taken:
            missing.append(entry)
        else:
            taken.add(entry.display_ref)
    if not missing:
        return entries
    result = dict(entries)
    for entry in missing:
        reference = allocate_reference(taken, generate=generate)
        taken.add(reference)
        result[entry.id] = entry.model_copy(update={"display_ref": reference})
    return result


def _read_private(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            return stream.read()
    finally:
        os.close(descriptor)


def _atomic_private_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            if os.name == "posix":
                os.chmod(temporary, 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError as error:
        raise EntryError("Der Verlauf konnte nicht gespeichert werden.") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


__all__ = [
    "MAX_EXPORTS",
    "DecisionState",
    "Entry",
    "EntryError",
    "EntryExport",
    "EntryFile",
    "EntryKind",
    "EntryNotFoundError",
    "EntryStatus",
    "EntryStore",
    "ExportedFile",
    "FileOutput",
    "FileReview",
    "FileStatus",
    "ManualState",
    "allocate_reference",
    "confirmation_key",
]
