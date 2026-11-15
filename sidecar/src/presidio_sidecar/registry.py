"""Encrypted master-data registry and Presidio recognizer (spec §6.2).

Persons and organizations stay separate from import through the recognizers.
A re-import becomes visible only after both source files are read
successfully and the new encrypted snapshot is stored atomically.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Final, Literal

import regex
from presidio_analyzer import PatternRecognizer, RecognizerResult

from .registry_crypto import RegistryCipher, RegistryCryptoError

PERSON_ENTITY: Final = "PERSON"
ORGANIZATION_ENTITY: Final = "ORGANIZATION"
DEFAULT_MIN_LENGTH: Final = 3
DENY_LIST_SCORE: Final = 1.0
STORE_SCHEMA_VERSION: Final = 2

RegistryEntity = Literal["PERSON", "ORGANIZATION"]

_PERSON_CONTAINER_KEYS = frozenset({"persons", "personen", "people"})
_ORGANIZATION_CONTAINER_KEYS = frozenset(
    {"organizations", "organisations", "organisationen", "orgs", "firmen", "companies"}
)
_GENERIC_CONTAINER_KEYS = frozenset({"names", "namen", "values", "werte", "entries"})
_GENERIC_NAME_KEYS = frozenset(
    {
        "name",
        "full_name",
        "fullname",
        "display_name",
        "bezeichnung",
        "wert",
        "value",
    }
)
_PERSON_NAME_KEYS = _GENERIC_NAME_KEYS | frozenset(
    {"person", "person_name", "personenname", "vor_und_nachname"}
)
_ORGANIZATION_NAME_KEYS = _GENERIC_NAME_KEYS | frozenset(
    {
        "organization",
        "organisation",
        "organization_name",
        "organisation_name",
        "firma",
        "firmenname",
        "company",
        "company_name",
    }
)


class RegistryError(ValueError):
    """Base class for expected registry errors."""


class RegistryImportError(RegistryError):
    """A CSV/JSON source is ambiguous or unreadable."""


class RegistryStorageError(RegistryError):
    """The encrypted registry snapshot is invalid."""


@dataclass(frozen=True)
class RegistrySources:
    """Separate import sources for persons and organizations."""

    persons: Path
    organizations: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "persons", Path(self.persons))
        object.__setattr__(self, "organizations", Path(self.organizations))

    @classmethod
    def from_directory(
        cls,
        directory: Path,
        *,
        persons_filename: str = "personen.csv",
        organizations_filename: str = "firmen.csv",
    ) -> RegistrySources:
        """Build the typical paths of a nightly ERP export."""

        base = Path(directory)
        return cls(
            persons=base / persons_filename,
            organizations=base / organizations_filename,
        )


@dataclass(frozen=True)
class RegistryStatus:
    """Compact state for health endpoint and UI status line."""

    persons_count: int
    organizations_count: int
    updated_at: datetime | None
    imported_at: datetime | None = None

    @property
    def total_count(self) -> int:
        return self.persons_count + self.organizations_count

    def as_dict(self) -> dict[str, int | str | None]:
        return {
            "persons_count": self.persons_count,
            "organizations_count": self.organizations_count,
            "total_count": self.total_count,
            "updated_at": self.updated_at.isoformat() if self.updated_at is not None else None,
            "imported_at": (self.imported_at.isoformat() if self.imported_at is not None else None),
        }


@dataclass(frozen=True)
class _RegistrySnapshot:
    persons: tuple[str, ...] = ()
    organizations: tuple[str, ...] = ()
    updated_at: datetime | None = None
    imported_at: datetime | None = None

    @property
    def status(self) -> RegistryStatus:
        return RegistryStatus(
            persons_count=len(self.persons),
            organizations_count=len(self.organizations),
            updated_at=self.updated_at,
            imported_at=self.imported_at,
        )


@dataclass(frozen=True)
class _SourceFileState:
    path: str
    device: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True)
class _NormalizedText:
    value: str
    starts: tuple[int, ...]
    ends: tuple[int, ...]

    def original_span(self, start: int, end: int) -> tuple[int, int]:
        if start < 0 or end <= start or end > len(self.value):
            raise ValueError("Ungueltiger Trefferbereich im normalisierten Text.")
        return self.starts[start], self.ends[end - 1]


def normalize_registry_value(value: str) -> str:
    """Normalize a list entry using NFKC and Unicode casefold.

    ``casefold`` provides full Unicode lower-casing, handling e.g. ``Straße``/``STRASSE``
    consistently.
    Leading/trailing whitespace is never part of a name.
    """

    if not isinstance(value, str):
        raise TypeError("Registry-Werte muessen Zeichenketten sein.")
    return unicodedata.normalize("NFKC", value).casefold().strip()


def _normalize_text_with_offsets(text: str) -> _NormalizedText:
    """Normalize text while preserving offsets for the original text."""

    normalized_parts: list[str] = []
    starts: list[int] = []
    ends: list[int] = []

    # ``\X`` splits into extended grapheme clusters. For example,
    # ``e`` + combining acute becomes NFKC-``é`` and the match still covers both original
    # codepoints.
    for grapheme in regex.finditer(r"\X", text):
        normalized = unicodedata.normalize("NFKC", grapheme.group()).casefold()
        normalized_parts.append(normalized)
        starts.extend([grapheme.start()] * len(normalized))
        ends.extend([grapheme.end()] * len(normalized))

    return _NormalizedText("".join(normalized_parts), tuple(starts), tuple(ends))


class NormalizedDenyListRecognizer(PatternRecognizer):
    """Presidio ``PatternRecognizer`` with NFKC/casefold matching.

    The deny list uses Presidio's native mechanism (score 1.0). Only the input text is normalized
    before matching;
    match offsets are later mapped back to the unchanged original text.
    """

    def __init__(
        self,
        *,
        supported_entity: RegistryEntity,
        deny_list: Sequence[str],
        name: str,
        supported_language: str = "de",
    ) -> None:
        normalized_values = _prepare_values(deny_list, min_length=DEFAULT_MIN_LENGTH)
        if not normalized_values:
            raise ValueError("Ein Deny-List-Recognizer benoetigt mindestens einen Namen.")
        super().__init__(
            supported_entity=supported_entity,
            name=name,
            supported_language=supported_language,
            deny_list=list(normalized_values),
            deny_list_score=DENY_LIST_SCORE,
            # Text is already casefold-normalized. DOTALL/MULTILINE flags are not needed for
            # Presidio-generated boundaries.
            global_regex_flags=0,
        )

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: Any = None,
        regex_flags: int | None = None,
    ) -> list[RecognizerResult]:
        normalized = _normalize_text_with_offsets(text)
        if not normalized.value:
            return []

        results = super().analyze(
            normalized.value,
            entities,
            nlp_artifacts=nlp_artifacts,
            regex_flags=regex_flags,
        )
        for result in results:
            result.start, result.end = normalized.original_span(result.start, result.end)
            metadata = dict(result.recognition_metadata or {})
            metadata["source"] = "master_data"
            result.recognition_metadata = metadata
        return results


class EncryptedRegistryStore:
    """Atomic encrypted persistence of a registry snapshot."""

    def __init__(self, path: Path, key: bytes) -> None:
        self.path = Path(path)
        self._cipher = RegistryCipher(key)

    def exists(self) -> bool:
        return self.path.is_file()

    def save(self, snapshot: _RegistrySnapshot) -> None:
        document = {
            "schema_version": STORE_SCHEMA_VERSION,
            "persons": list(snapshot.persons),
            "organizations": list(snapshot.organizations),
            "updated_at": (
                snapshot.updated_at.isoformat() if snapshot.updated_at is not None else None
            ),
            "imported_at": (
                snapshot.imported_at.isoformat() if snapshot.imported_at is not None else None
            ),
        }
        plaintext = json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        payload = self._cipher.encrypt(plaintext)
        self._write_atomically(payload)

    def load(self) -> _RegistrySnapshot:
        try:
            payload = self.path.read_bytes()
            plaintext = self._cipher.decrypt(payload)
            document = json.loads(plaintext)
            return _snapshot_from_document(document)
        except FileNotFoundError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, RegistryCryptoError) as error:
            raise RegistryStorageError("Registry-Abbild konnte nicht geladen werden.") from error

    def _write_atomically(self, payload: bytes) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                os.chmod(temporary_path, 0o600)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


class MasterDataRegistry:
    """In-memory registry with encrypted snapshot and re-import API."""

    def __init__(
        self,
        storage_path: Path,
        key: bytes,
        *,
        min_length: int = DEFAULT_MIN_LENGTH,
        nightly_sources: RegistrySources | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if min_length < DEFAULT_MIN_LENGTH:
            raise ValueError(
                f"min_length darf aus Schutzgruenden nicht kleiner als {DEFAULT_MIN_LENGTH} sein."
            )
        self._min_length = min_length
        self._nightly_sources = nightly_sources
        self._clock = clock or (lambda: datetime.now(UTC))
        self._store = EncryptedRegistryStore(storage_path, key)
        self._lock = RLock()
        self._snapshot = self._load_existing()

    @property
    def nightly_sources(self) -> RegistrySources | None:
        return self._nightly_sources

    def status(self) -> RegistryStatus:
        with self._lock:
            return self._snapshot.status

    def manual_reimport(
        self,
        *,
        persons_path: Path,
        organizations_path: Path,
    ) -> RegistryStatus:
        """Re-import explicitly selected CSV/JSON files."""

        return self.reimport(
            RegistrySources(
                persons=persons_path,
                organizations=organizations_path,
            )
        )

    def reimport_nightly(self) -> RegistryStatus:
        """Re-import from the configured nightly export paths."""

        if self._nightly_sources is None:
            raise RegistryImportError("Keine Quellen fuer den naechtlichen Export konfiguriert.")
        return self.reimport(self._nightly_sources)

    def reimport(self, sources: RegistrySources) -> RegistryStatus:
        """Import both entity lists as a single atomic update."""

        source_state = _capture_source_state(sources)
        combined_source = sources.persons.resolve() == sources.organizations.resolve()
        if combined_source and sources.persons.suffix.casefold() != ".json":
            raise RegistryImportError(
                "Eine gemeinsame Stammdatenquelle muss ein JSON-Objekt mit "
                "getrennten Personen- und Organisationslisten sein."
            )
        if combined_source:
            raw_persons, raw_organizations = _load_combined_json_values(sources.persons)
        else:
            raw_persons = load_registry_values(
                sources.persons,
                entity=PERSON_ENTITY,
            )
            raw_organizations = load_registry_values(
                sources.organizations,
                entity=ORGANIZATION_ENTITY,
            )

        persons = _prepare_values(raw_persons, min_length=self._min_length)
        organizations = _prepare_values(
            raw_organizations,
            min_length=self._min_length,
        )
        if _capture_source_state(sources) != source_state:
            raise RegistryImportError(
                "Stammdatenquelle wurde waehrend des Imports geaendert; "
                "der bisherige Stand bleibt aktiv."
            )
        imported_at = self._current_time()
        source_updated_at = min(
            datetime.fromtimestamp(item.modified_ns / 1_000_000_000, tz=UTC)
            for item in source_state
        )
        snapshot = _RegistrySnapshot(
            persons=persons,
            organizations=organizations,
            updated_at=source_updated_at,
            imported_at=imported_at,
        )

        # First replace the persistent snapshot, then switch the state used by
        # ongoing analyses. If a source fails or writing fails, keep the previous
        # registry intact.
        with self._lock:
            self._store.save(snapshot)
            self._snapshot = snapshot
        return snapshot.status

    def recognizers(self, *, supported_language: str = "de") -> tuple[PatternRecognizer, ...]:
        """Create separate Presidio recognizers for the current state."""

        with self._lock:
            snapshot = self._snapshot

        recognizers: list[PatternRecognizer] = []
        if snapshot.persons:
            recognizers.append(
                NormalizedDenyListRecognizer(
                    supported_entity=PERSON_ENTITY,
                    deny_list=snapshot.persons,
                    name="MasterDataPersonRecognizer",
                    supported_language=supported_language,
                )
            )
        if snapshot.organizations:
            recognizers.append(
                NormalizedDenyListRecognizer(
                    supported_entity=ORGANIZATION_ENTITY,
                    deny_list=snapshot.organizations,
                    name="MasterDataOrganizationRecognizer",
                    supported_language=supported_language,
                )
            )
        return tuple(recognizers)

    def _load_existing(self) -> _RegistrySnapshot:
        if not self._store.exists():
            return _RegistrySnapshot()
        snapshot = self._store.load()
        return _RegistrySnapshot(
            persons=_prepare_values(snapshot.persons, min_length=self._min_length),
            organizations=_prepare_values(
                snapshot.organizations,
                min_length=self._min_length,
            ),
            updated_at=snapshot.updated_at,
            imported_at=snapshot.imported_at,
        )

    def _current_time(self) -> datetime:
        current = self._clock()
        if current.tzinfo is None or current.utcoffset() is None:
            raise RegistryError("Der Registry-Zeitstempel muss eine Zeitzone enthalten.")
        return current.astimezone(UTC)


def load_registry_values(
    path: Path,
    *,
    entity: RegistryEntity,
    allow_generic_json: bool = True,
) -> list[str]:
    """Read names from a CSV or JSON source."""

    source = Path(path)
    suffix = source.suffix.casefold()
    try:
        if suffix == ".csv":
            return _load_csv_values(source, entity=entity)
        if suffix == ".json":
            return _load_json_values(
                source,
                entity=entity,
                allow_generic=allow_generic_json,
            )
    except OSError as error:
        raise RegistryImportError(f"Importquelle {source} konnte nicht gelesen werden.") from error
    raise RegistryImportError(f"Importquelle {source} hat kein unterstuetztes CSV-/JSON-Format.")


def _prepare_values(values: Iterable[str], *, min_length: int) -> tuple[str, ...]:
    prepared: set[str] = set()
    for value in values:
        normalized = normalize_registry_value(value)
        if len(normalized) >= min_length:
            prepared.add(normalized)
    # Longer entries first: for ``Anna`` and ``Anna Maria`` Presidio's regex must not stop at the
    # shorter alternative.
    return tuple(sorted(prepared, key=lambda item: (-len(item), item)))


def _load_csv_values(path: Path, *, entity: RegistryEntity) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        sample = stream.read(8192)
        stream.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = [row for row in csv.reader(stream, dialect) if any(cell.strip() for cell in row)]

    if not rows:
        return []

    first_row = rows[0]
    allowed_headers = _name_keys(entity)
    normalized_headers = [_key(cell) for cell in first_row]
    matching_columns = [
        index for index, header in enumerate(normalized_headers) if header in allowed_headers
    ]

    if matching_columns:
        column = matching_columns[0]
        data_rows = rows[1:]
    elif max(len(row) for row in rows) == 1:
        column = 0
        data_rows = rows
    else:
        raise RegistryImportError(
            f"CSV {path} braucht eine eindeutige Namensspalte "
            f"({', '.join(sorted(allowed_headers))})."
        )

    return [row[column] for row in data_rows if len(row) > column and row[column].strip()]


def _load_json_values(
    path: Path,
    *,
    entity: RegistryEntity,
    allow_generic: bool,
) -> list[str]:
    payload = _read_json_payload(path)
    return _json_values_from_payload(
        payload,
        entity=entity,
        path=path,
        allow_generic=allow_generic,
    )


def _read_json_payload(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8-sig") as stream:
            return json.load(stream)
    except json.JSONDecodeError as error:
        raise RegistryImportError(f"JSON {path} ist ungueltig.") from error


def _json_values_from_payload(
    payload: Any,
    *,
    entity: RegistryEntity,
    path: Path,
    allow_generic: bool,
) -> list[str]:
    items = _select_json_items(
        payload,
        entity=entity,
        path=path,
        allow_generic=allow_generic,
    )
    values: list[str] = []
    allowed_keys = _name_keys(entity)
    for index, item in enumerate(items):
        if isinstance(item, str):
            values.append(item)
            continue
        if isinstance(item, dict):
            normalized_item = {_key(str(key)): value for key, value in item.items()}
            matching_keys = [key for key in normalized_item if key in allowed_keys]
            if matching_keys and isinstance(normalized_item[matching_keys[0]], str):
                values.append(normalized_item[matching_keys[0]])
                continue
        raise RegistryImportError(
            f"JSON {path}: Eintrag {index} enthaelt keinen eindeutigen Namen."
        )
    return values


def _load_combined_json_values(path: Path) -> tuple[list[str], list[str]]:
    try:
        payload = _read_json_payload(path)
    except OSError as error:
        raise RegistryImportError(f"Importquelle {path} konnte nicht gelesen werden.") from error
    return (
        _json_values_from_payload(
            payload,
            entity=PERSON_ENTITY,
            path=path,
            allow_generic=False,
        ),
        _json_values_from_payload(
            payload,
            entity=ORGANIZATION_ENTITY,
            path=path,
            allow_generic=False,
        ),
    )


def _select_json_items(
    payload: Any,
    *,
    entity: RegistryEntity,
    path: Path,
    allow_generic: bool,
) -> list[Any]:
    if isinstance(payload, list):
        if not allow_generic:
            raise RegistryImportError(
                f"JSON {path} braucht getrennte Personen- und Organisationslisten."
            )
        return payload
    if not isinstance(payload, dict):
        raise RegistryImportError(f"JSON {path} muss eine Liste oder ein Objekt enthalten.")

    normalized = {_key(str(key)): value for key, value in payload.items()}
    entity_keys = (
        _PERSON_CONTAINER_KEYS if entity == PERSON_ENTITY else _ORGANIZATION_CONTAINER_KEYS
    )
    candidate_keys = (
        (*entity_keys, *_GENERIC_CONTAINER_KEYS) if allow_generic else tuple(entity_keys)
    )
    for key in candidate_keys:
        value = normalized.get(key)
        if isinstance(value, list):
            return value
    raise RegistryImportError(
        f"JSON {path} enthaelt keine getrennte Liste fuer {entity.casefold()}."
    )


def _name_keys(entity: RegistryEntity) -> frozenset[str]:
    return _PERSON_NAME_KEYS if entity == PERSON_ENTITY else _ORGANIZATION_NAME_KEYS


def _key(value: str) -> str:
    normalized = normalize_registry_value(value)
    return normalized.replace("-", "_").replace(" ", "_")


def _capture_source_state(sources: RegistrySources) -> tuple[_SourceFileState, ...]:
    states: list[_SourceFileState] = []
    seen: set[str] = set()
    for source in (sources.persons, sources.organizations):
        resolved = str(source.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            metadata = source.stat()
        except OSError as error:
            raise RegistryImportError(
                "Mindestens eine Stammdatenquelle konnte nicht gelesen werden."
            ) from error
        states.append(
            _SourceFileState(
                path=resolved,
                device=metadata.st_dev,
                inode=metadata.st_ino,
                size=metadata.st_size,
                modified_ns=metadata.st_mtime_ns,
                changed_ns=metadata.st_ctime_ns,
            )
        )
    return tuple(states)


def _snapshot_from_document(document: Any) -> _RegistrySnapshot:
    try:
        if not isinstance(document, dict) or document["schema_version"] not in {1, 2}:
            raise ValueError("Unbekannte Schema-Version.")
        schema_version = document["schema_version"]
        persons = document["persons"]
        organizations = document["organizations"]
        if not isinstance(persons, list) or not all(isinstance(item, str) for item in persons):
            raise TypeError("persons ist keine String-Liste.")
        if not isinstance(organizations, list) or not all(
            isinstance(item, str) for item in organizations
        ):
            raise TypeError("organizations ist keine String-Liste.")
        if schema_version == 1:
            updated_at = None
            imported_at = _parse_timestamp(document["updated_at"])
        else:
            updated_at = _parse_timestamp(document["updated_at"])
            imported_at = _parse_timestamp(document["imported_at"])
    except (KeyError, TypeError, ValueError) as error:
        raise RegistryStorageError("Registry-Abbild hat ein ungueltiges Schema.") from error

    return _RegistrySnapshot(
        persons=tuple(persons),
        organizations=tuple(organizations),
        updated_at=updated_at,
        imported_at=imported_at,
    )


def _parse_timestamp(raw_timestamp: Any) -> datetime | None:
    if raw_timestamp is None:
        return None
    if not isinstance(raw_timestamp, str):
        raise TypeError("Zeitstempel ist keine Zeichenkette.")
    timestamp = datetime.fromisoformat(raw_timestamp)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Zeitstempel hat keine Zeitzone.")
    return timestamp.astimezone(UTC)
