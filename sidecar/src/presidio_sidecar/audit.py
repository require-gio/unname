"""Content-free SQLite audit log for document exports and copied texts.

The API deliberately does not accept original values, replacements, text snippets,
or file paths. The export hash is computed directly from the final file so that
no clear-text fields ever reach the database. In text-mode copies there is no file;
the hash of the copied text (``kind = text``) is stored instead.

Since API v2 each record also includes the masking request (``AuditContext``):
entry ID, method, style, categories and the decisions by origin – user, AI
suggestion and “masked without decision” (identifiers and counters only)."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

from .exporting import sha256_file

_PROFILE_VERSION_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}\Z")
_ENTITY_TYPE_RE: Final = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_FINDING_ID_RE: Final = re.compile(r"f_[0-9a-f]{20}\Z")
_ENTRY_ID_RE: Final = re.compile(r"[0-9a-f]{8,64}\Z")
_CATEGORY_RE: Final = re.compile(r"[a-z][a-z_]{0,31}\Z")
_ALLOWED_ORIGINS: Final = frozenset({"user", "ai-suggestion"})
_ALLOWED_METHODS: Final = frozenset({"reversible", "irreversible"})
_ALLOWED_STYLES: Final = frozenset({"codes", "surrogates", "placeholder", "mask", "remove"})
_ALLOWED_KINDS: Final = frozenset({"document", "text"})
_ALLOWED_MODES: Final = frozenset(
    {
        "token_deterministic",
        "surrogate",
        "encrypt",
        "hash",
        "placeholder",
        "mask",
        "remove",
        "inherit",
    }
)


class AuditError(RuntimeError):
    """Base class for controllable audit errors."""


class AuditValidationError(AuditError, ValueError):
    """Audit metadata does not match the content-free schema."""


class AuditAction(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    KEEP_PLAIN = "keep_plain"
    FORCE_HANDLE = "force_handle"
    REMOVE_KEEP_PLAIN = "remove_keep_plain"
    REMOVE_FORCE_HANDLE = "remove_force_handle"
    CHANGE_MODE = "change_mode"
    ADD_MANUAL = "add_manual"
    ADJUST_SPAN = "adjust_span"


@dataclass(frozen=True, slots=True)
class AuditDecision:
    """A user decision without value, context, or text position."""

    entity_type: str
    action: AuditAction
    finding_id: str | None = None
    mode: str | None = None
    #: Who decided: ``user`` or ``ai-suggestion`` (API v2).
    origin: str | None = None

    def __post_init__(self) -> None:
        entity_type = _validate_entity_type(self.entity_type)
        try:
            action = AuditAction(self.action)
        except (TypeError, ValueError) as error:
            raise AuditValidationError("Unbekannte Audit-Entscheidung.") from error
        if self.finding_id is not None and not _FINDING_ID_RE.fullmatch(self.finding_id):
            raise AuditValidationError("Audit-finding_id hat nicht das inhaltsfreie ID-Format.")
        if self.mode is not None and self.mode not in _ALLOWED_MODES:
            raise AuditValidationError("Unbekannter Verarbeitungsmodus im Auditlog.")
        if self.origin is not None and self.origin not in _ALLOWED_ORIGINS:
            raise AuditValidationError("Unbekannte Herkunft einer Audit-Entscheidung.")
        object.__setattr__(self, "entity_type", entity_type)
        object.__setattr__(self, "action", action)


@dataclass(frozen=True, slots=True)
class AuditContext:
    """The masking request of a save (API v2) – identifiers and counters only."""

    entry_id: str
    method: str
    style: str
    categories: tuple[str, ...]
    protection_level: int
    decided_by_user: int = 0
    decided_by_ai: int = 0
    masked_without_decision: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.entry_id, str) or not _ENTRY_ID_RE.fullmatch(self.entry_id):
            raise AuditValidationError("Eintrags-ID hat kein erlaubtes Metadatenformat.")
        if self.method not in _ALLOWED_METHODS or self.style not in _ALLOWED_STYLES:
            raise AuditValidationError("Unbekannte Methode oder unbekannter Stil im Auditlog.")
        categories = tuple(self.categories)
        if not all(isinstance(item, str) and _CATEGORY_RE.fullmatch(item) for item in categories):
            raise AuditValidationError("Kategorie hat kein erlaubtes Metadatenformat.")
        if self.protection_level not in {1, 2, 3}:
            raise AuditValidationError("Ungueltige Schutzstufe im Auditlog.")
        for count in (self.decided_by_user, self.decided_by_ai, self.masked_without_decision):
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise AuditValidationError("Zaehlwerke muessen nichtnegative Ganzzahlen sein.")
        object.__setattr__(self, "categories", categories)


@dataclass(frozen=True, slots=True)
class AuditRecord:
    id: int
    timestamp: datetime
    profile_version: str
    findings_by_entity: Mapping[str, int]
    decisions: tuple[AuditDecision, ...]
    export_sha256: str
    kind: str = "document"
    context: AuditContext | None = None


class AuditLog:
    """Small thread-safe SQLite store for audit metadata only."""

    def __init__(
        self,
        path: Path | str,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path)
        if self.path.exists() and not self.path.is_file():
            raise AuditValidationError("Auditpfad ist keine Datei.")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        try:
            self._connection = sqlite3.connect(self.path, check_same_thread=False)
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = DELETE")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._create_schema()
            _restrict_permissions(self.path, 0o600)
        except (OSError, sqlite3.Error) as error:
            raise AuditError("Auditlog konnte nicht sicher geoeffnet werden.") from error

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> AuditLog:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def record_export(
        self,
        export_path: Path | str,
        *,
        profile_version: str,
        findings_by_entity: Mapping[str, int],
        decisions: Sequence[AuditDecision] = (),
        context: AuditContext | None = None,
    ) -> AuditRecord:
        """Writes exactly one complete export record atomically."""

        export = Path(export_path)
        if not export.is_file():
            raise AuditValidationError("Exportdatei fuer Audit-Hash wurde nicht gefunden.")
        return self._record(
            kind="document",
            output_sha256=sha256_file(export),
            profile_version=profile_version,
            findings_by_entity=findings_by_entity,
            decisions=decisions,
            context=context,
        )

    def record_text(
        self,
        output_text: str,
        *,
        profile_version: str,
        findings_by_entity: Mapping[str, int],
        decisions: Sequence[AuditDecision] = (),
        context: AuditContext | None = None,
    ) -> AuditRecord:
        """\"Copy\" in text mode: hash of the provided text instead of a file."""

        if not isinstance(output_text, str):
            raise AuditValidationError("Der kopierte Text muss eine Zeichenkette sein.")
        return self._record(
            kind="text",
            output_sha256=hashlib.sha256(output_text.encode("utf-8")).hexdigest(),
            profile_version=profile_version,
            findings_by_entity=findings_by_entity,
            decisions=decisions,
            context=context,
        )

    def _record(
        self,
        *,
        kind: str,
        output_sha256: str,
        profile_version: str,
        findings_by_entity: Mapping[str, int],
        decisions: Sequence[AuditDecision],
        context: AuditContext | None,
    ) -> AuditRecord:
        if kind not in _ALLOWED_KINDS:
            raise AuditValidationError("Unbekannte Art eines Auditdatensatzes.")
        if not isinstance(profile_version, str) or not _PROFILE_VERSION_RE.fullmatch(
            profile_version
        ):
            raise AuditValidationError("Profilversion hat kein erlaubtes Metadatenformat.")
        normalized_counts = _validate_counts(findings_by_entity)
        normalized_decisions = tuple(decisions)
        if not all(isinstance(item, AuditDecision) for item in normalized_decisions):
            raise AuditValidationError("decisions darf nur AuditDecision enthalten.")
        if context is not None and not isinstance(context, AuditContext):
            raise AuditValidationError("context muss ein AuditContext sein.")
        timestamp = self._clock()
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise AuditValidationError("Audit-Zeitpunkt muss eine Zeitzone enthalten.")
        timestamp = timestamp.astimezone(UTC)

        with self._lock:
            try:
                with self._connection:
                    cursor = self._connection.execute(
                        """
                        INSERT INTO audit_exports(
                            timestamp_utc, profile_version, export_sha256, kind
                        )
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            timestamp.isoformat().replace("+00:00", "Z"),
                            profile_version,
                            output_sha256,
                            kind,
                        ),
                    )
                    export_id = int(cursor.lastrowid)
                    self._connection.executemany(
                        """
                        INSERT INTO audit_entity_counts(export_id, entity_type, finding_count)
                        VALUES (?, ?, ?)
                        """,
                        (
                            (export_id, entity_type, count)
                            for entity_type, count in normalized_counts.items()
                        ),
                    )
                    self._connection.executemany(
                        """
                        INSERT INTO audit_decisions(
                            export_id, sequence_no, finding_id, entity_type, action, mode, origin
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            (
                                export_id,
                                sequence,
                                decision.finding_id,
                                decision.entity_type,
                                str(decision.action),
                                decision.mode,
                                decision.origin,
                            )
                            for sequence, decision in enumerate(normalized_decisions)
                        ),
                    )
                    if context is not None:
                        self._connection.execute(
                            """
                            INSERT INTO audit_masking(
                                export_id, entry_id, method, style, categories,
                                protection_level, decided_by_user, decided_by_ai,
                                masked_without_decision
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                export_id,
                                context.entry_id,
                                context.method,
                                context.style,
                                ",".join(context.categories),
                                context.protection_level,
                                context.decided_by_user,
                                context.decided_by_ai,
                                context.masked_without_decision,
                            ),
                        )
            except sqlite3.Error as error:
                raise AuditError(
                    "Auditdatensatz konnte nicht atomar gespeichert werden."
                ) from error

        return AuditRecord(
            id=export_id,
            timestamp=timestamp,
            profile_version=profile_version,
            findings_by_entity=normalized_counts,
            decisions=normalized_decisions,
            export_sha256=output_sha256,
            kind=kind,
            context=context,
        )

    def get(self, export_id: int) -> AuditRecord:
        """Reads a record; the return value cannot carry plaintext by design."""

        if isinstance(export_id, bool) or export_id < 1:
            raise AuditValidationError("Ungueltige Audit-ID.")
        with self._lock:
            row = self._connection.execute(
                """
                SELECT timestamp_utc, profile_version, export_sha256, kind
                FROM audit_exports
                WHERE id = ?
                """,
                (export_id,),
            ).fetchone()
            if row is None:
                raise KeyError(export_id)
            count_rows = self._connection.execute(
                """
                SELECT entity_type, finding_count
                FROM audit_entity_counts
                WHERE export_id = ?
                ORDER BY entity_type
                """,
                (export_id,),
            ).fetchall()
            decision_rows = self._connection.execute(
                """
                SELECT entity_type, action, finding_id, mode, origin
                FROM audit_decisions
                WHERE export_id = ?
                ORDER BY sequence_no
                """,
                (export_id,),
            ).fetchall()
            masking_row = self._connection.execute(
                """
                SELECT entry_id, method, style, categories, protection_level,
                       decided_by_user, decided_by_ai, masked_without_decision
                FROM audit_masking
                WHERE export_id = ?
                """,
                (export_id,),
            ).fetchone()
        timestamp = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
        return AuditRecord(
            id=export_id,
            timestamp=timestamp,
            profile_version=str(row[1]),
            findings_by_entity={str(entity): int(count) for entity, count in count_rows},
            decisions=tuple(
                AuditDecision(
                    entity_type=str(entity),
                    action=AuditAction(action),
                    finding_id=(str(finding_id) if finding_id is not None else None),
                    mode=(str(mode) if mode is not None else None),
                    origin=(str(origin) if origin is not None else None),
                )
                for entity, action, finding_id, mode, origin in decision_rows
            ),
            export_sha256=str(row[2]),
            kind=str(row[3]),
            context=(
                AuditContext(
                    entry_id=str(masking_row[0]),
                    method=str(masking_row[1]),
                    style=str(masking_row[2]),
                    categories=tuple(item for item in str(masking_row[3]).split(",") if item),
                    protection_level=int(masking_row[4]),
                    decided_by_user=int(masking_row[5]),
                    decided_by_ai=int(masking_row[6]),
                    masked_without_decision=int(masking_row[7]),
                )
                if masking_row is not None
                else None
            ),
        )

    def _create_schema(self) -> None:
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS audit_exports (
                    id INTEGER PRIMARY KEY,
                    timestamp_utc TEXT NOT NULL,
                    profile_version TEXT NOT NULL,
                    export_sha256 TEXT NOT NULL
                        CHECK(length(export_sha256) = 64)
                );

                CREATE TABLE IF NOT EXISTS audit_entity_counts (
                    export_id INTEGER NOT NULL
                        REFERENCES audit_exports(id) ON DELETE CASCADE,
                    entity_type TEXT NOT NULL,
                    finding_count INTEGER NOT NULL CHECK(finding_count >= 0),
                    PRIMARY KEY(export_id, entity_type)
                );

                CREATE TABLE IF NOT EXISTS audit_decisions (
                    export_id INTEGER NOT NULL
                        REFERENCES audit_exports(id) ON DELETE CASCADE,
                    sequence_no INTEGER NOT NULL CHECK(sequence_no >= 0),
                    finding_id TEXT,
                    entity_type TEXT NOT NULL,
                    action TEXT NOT NULL,
                    mode TEXT,
                    PRIMARY KEY(export_id, sequence_no)
                );

                CREATE TABLE IF NOT EXISTS audit_masking (
                    export_id INTEGER PRIMARY KEY
                        REFERENCES audit_exports(id) ON DELETE CASCADE,
                    entry_id TEXT NOT NULL,
                    method TEXT NOT NULL,
                    style TEXT NOT NULL,
                    categories TEXT NOT NULL,
                    protection_level INTEGER NOT NULL,
                    decided_by_user INTEGER NOT NULL CHECK(decided_by_user >= 0),
                    decided_by_ai INTEGER NOT NULL CHECK(decided_by_ai >= 0),
                    masked_without_decision INTEGER NOT NULL
                        CHECK(masked_without_decision >= 0)
                );
                """
            )
            # Add API v2 columns to an existing log; old records remain valid (kind
            # "document", no origin).
            if "kind" not in self._columns("audit_exports"):
                self._connection.execute(
                    "ALTER TABLE audit_exports ADD COLUMN kind TEXT NOT NULL DEFAULT 'document'"
                )
            if "origin" not in self._columns("audit_decisions"):
                self._connection.execute("ALTER TABLE audit_decisions ADD COLUMN origin TEXT")

    def _columns(self, table: str) -> set[str]:
        return {str(row[1]) for row in self._connection.execute(f"PRAGMA table_info({table})")}


def _validate_entity_type(raw: str) -> str:
    if not isinstance(raw, str):
        raise AuditValidationError("Entitaetstyp muss Text sein.")
    entity_type = raw.strip().upper()
    if not _ENTITY_TYPE_RE.fullmatch(entity_type):
        raise AuditValidationError("Entitaetstyp hat kein erlaubtes Metadatenformat.")
    return entity_type


def _validate_counts(findings_by_entity: Mapping[str, int]) -> dict[str, int]:
    if not isinstance(findings_by_entity, Mapping):
        raise AuditValidationError("Trefferzahlen muessen eine Zuordnung sein.")
    result: dict[str, int] = {}
    for raw_entity_type, count in findings_by_entity.items():
        entity_type = _validate_entity_type(raw_entity_type)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise AuditValidationError("Trefferzahlen muessen nichtnegative Ganzzahlen sein.")
        if entity_type in result:
            raise AuditValidationError("Entitaetstyp darf nur einmal gezaehlt werden.")
        result[entity_type] = count
    return dict(sorted(result.items()))


def _restrict_permissions(path: Path, mode: int) -> None:
    if os.name != "nt":
        path.chmod(mode)
