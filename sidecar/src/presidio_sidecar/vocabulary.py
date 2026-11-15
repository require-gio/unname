"""Names & terms: custom name lists, terms, patterns and templates.

Everything here describes what should always be masked in addition:

* **Name lists** – persons or companies from a CSV, JSON or
  text file (customers, suppliers). They act like master data (level 0):
  a match is certain, even where name detection fails.
* **Custom terms** – single words or phrases (“Projekt Falke”).
* **Custom patterns** – regular expressions for numbers with fixed format
  (“POL-\\d{6,8}”). They run with the sidecar’s regex engine and a timeout; a pattern
  that fails the catastrophic reset test or is too broad is not stored.
* **Templates** – terms, patterns and optionally a masking style under one name;
  exportable/importable as YAML (``templates.py``).

The lists contain customer names. The store is kept like history and
registry solely as an AES-256-GCM ciphertext on disk, with its own
key (0600) and its own format identifier as Additional Authenticated Data.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal

import regex
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from presidio_analyzer import EntityRecognizer, RecognizerResult
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .registry import (
    DEFAULT_MIN_LENGTH,
    NormalizedDenyListRecognizer,
    RegistryError,
    load_registry_values,
    normalize_registry_value,
)
from .registry_crypto import load_or_create_registry_key
from .settings_store import MaskingDefaults

FORMAT_HEADER: Final = b"UNNAME-VOCABULARY\x01"
STORE_SCHEMA_VERSION: Final = 1
NONCE_SIZE: Final = 12

#: What a term or pattern becomes – same types as “type of entry”
#: when marking, plus the case number. The type determines category and
#: placeholder (e.g. [Kundennummer], [Aktenzeichen] …).
TermType = Literal["PERSON", "ORGANIZATION", "LOCATION", "CUSTOMER_NUMBER", "CASE_NUMBER", "OTHER"]
ListKind = Literal["persons", "organizations"]

MAX_LISTS: Final = 32
MAX_LIST_VALUES: Final = 200_000
MAX_TERMS: Final = 500
MAX_PATTERNS: Final = 100
MAX_TEMPLATES: Final = 50
MAX_PATTERN_LENGTH: Final = 500
#: After this age the UI asks “please update”.
LIST_STALE_AFTER: Final = timedelta(days=7)
#: Timeout per pattern and text during detection. A pattern that exceeds
#: the limit causes detection to fail – better no result than a silent gap.
PATTERN_TIMEOUT_SECONDS: Final = 2.0
#: Timeout when reviewing a new pattern against the probe texts.
_PROBE_TIMEOUT_SECONDS: Final = 0.25
_PROBES: Final = (
    "a" * 3000 + "!",
    "1" * 3000 + "x",
    " " * 3000 + "-",
    "aA1-_./ " * 400,
)
#: Typical sentence: a pattern that matches more than half of it
#: would mask whole documents (negative test, spec §11).
_ORDINARY_TEXT: Final = (
    "Sehr geehrte Damen und Herren, vielen Dank für Ihre Nachricht vom Montag. "
    "Wir melden uns in den nächsten Tagen mit weiteren Informationen zurück."
)


class VocabularyError(RuntimeError):
    """The store for names & terms could not operate securely."""


class VocabularyValidationError(ValueError):
    """A list, a term, or a pattern is invalid.

    ``code`` is shown in the UI; ``index`` gives the position in the request
    for patterns and terms.
    """

    def __init__(self, code: str, message: str, *, index: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.index = index


class VocabularyNotFoundError(VocabularyError, LookupError):
    pass


class PatternTimeoutError(VocabularyError):
    """A custom pattern exceeded its timeout."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NameList(_Model):
    id: str
    kind: ListKind
    #: Filename of the source (“kunden.csv”); the UI shows it as
    #: “Personen · kunden.csv”. Only the shell knows the path.
    source_name: str = Field(min_length=1, max_length=200)
    values: tuple[str, ...] = Field(default=(), repr=False)
    imported_at: datetime
    #: Modification time of the source file, if reported by the shell.
    source_modified_at: datetime | None = None

    @property
    def updated_at(self) -> datetime:
        return self.source_modified_at or self.imported_at


class Term(_Model):
    id: str
    text: str = Field(min_length=DEFAULT_MIN_LENGTH, max_length=200)
    entity_type: TermType


class Pattern(_Model):
    id: str
    name: str = Field(min_length=1, max_length=80)
    regex: str = Field(min_length=1, max_length=MAX_PATTERN_LENGTH)
    entity_type: TermType = "CUSTOMER_NUMBER"
    enabled: bool = True


class Template(_Model):
    id: str
    title: str = Field(min_length=1, max_length=120)
    terms: tuple[Term, ...] = ()
    patterns: tuple[Pattern, ...] = ()
    masking: MaskingDefaults | None = None
    created_at: datetime


class _Document(_Model):
    schema_version: Literal[1] = STORE_SCHEMA_VERSION
    lists: tuple[NameList, ...] = ()
    terms: tuple[Term, ...] = ()
    patterns: tuple[Pattern, ...] = ()
    templates: tuple[Template, ...] = ()


class TermIn(_Model):
    text: str = Field(min_length=1, max_length=200)
    entity_type: TermType

    @field_validator("text")
    @classmethod
    def _strip(cls, value: str) -> str:
        return " ".join(value.split())


class PatternIn(_Model):
    id: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    regex: str = Field(min_length=1, max_length=MAX_PATTERN_LENGTH)
    entity_type: TermType = "CUSTOMER_NUMBER"
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = " ".join(value.split())
        if not stripped:
            raise ValueError("Ein Muster braucht einen Namen.")
        return stripped


# -- Review ------------------------------------------------------------------


def compile_pattern(source: str, *, index: int | None = None) -> regex.Pattern[str]:
    """Compile a custom pattern and validate it before use.

    Reject patterns that are invalid, match the empty string (e.g. ``\\d*``),
    exceed the probe timeout on a test text (catastrophic backtracking such as
    ``(a+)+$``), or would mask a typical sentence in half (``\\w+``).
    """

    if not isinstance(source, str) or not source or len(source) > MAX_PATTERN_LENGTH:
        raise VocabularyValidationError(
            "pattern_invalid", "Das Muster ist leer oder zu lang.", index=index
        )
    try:
        compiled = regex.compile(source)
    except (regex.error, ValueError, OverflowError, RecursionError) as error:
        raise VocabularyValidationError(
            "pattern_invalid", "Das Muster ist kein gueltiger Ausdruck.", index=index
        ) from error
    try:
        if compiled.fullmatch("", timeout=_PROBE_TIMEOUT_SECONDS) is not None:
            raise VocabularyValidationError(
                "pattern_empty", "Das Muster trifft auch eine leere Stelle.", index=index
            )
        for probe in _PROBES:
            for _ in compiled.finditer(probe, timeout=_PROBE_TIMEOUT_SECONDS):
                pass
        covered = sum(
            match.end() - match.start()
            for match in compiled.finditer(_ORDINARY_TEXT, timeout=_PROBE_TIMEOUT_SECONDS)
        )
    except TimeoutError as error:
        raise VocabularyValidationError(
            "pattern_slow", "Das Muster ist zu aufwendig auszuwerten.", index=index
        ) from error
    if covered * 2 > len(_ORDINARY_TEXT):
        raise VocabularyValidationError(
            "pattern_broad", "Das Muster trifft fast jeden Text.", index=index
        )
    return compiled


def parse_name_list(name: str, content: bytes, kind: ListKind) -> tuple[str, ...]:
    """Read names from a CSV, JSON, or plain-text file.

    CSV and JSON use the same formats as the master-data import (a name column
    or a list under ``personen``/``firmen``/``names``). A ``.txt`` file contains
    one name per line.
    """

    suffix = Path(name).suffix.casefold()
    entity = "PERSON" if kind == "persons" else "ORGANIZATION"
    if suffix == ".txt":
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise VocabularyValidationError(
                "list_encoding", "Die Datei ist kein UTF-8-Text."
            ) from error
        raw = [line.strip() for line in text.splitlines()]
    elif suffix in {".csv", ".json"}:
        # The master-data import reads files; the bytes come here from the shell.
        # A private short-lived folder holds them for the import.
        with tempfile.TemporaryDirectory(prefix="unname-list-") as directory:
            os.chmod(directory, 0o700)
            path = Path(directory) / f"liste{suffix}"
            path.write_bytes(content)
            try:
                raw = load_registry_values(path, entity=entity)
            except (RegistryError, UnicodeDecodeError) as error:
                raise VocabularyValidationError(
                    "list_format",
                    "Die Datei enthaelt keine eindeutige Namensspalte oder -liste.",
                ) from error
    else:
        raise VocabularyValidationError(
            "list_unsupported", "Namenslisten koennen CSV, JSON oder Text sein."
        )
    values = tuple(
        dict.fromkeys(
            value
            for item in raw
            if isinstance(item, str)
            and len(normalize_registry_value(value := " ".join(item.split()))) >= DEFAULT_MIN_LENGTH
        )
    )
    if not values:
        raise VocabularyValidationError("list_empty", "In der Datei steht kein Name.")
    if len(values) > MAX_LIST_VALUES:
        raise VocabularyValidationError("list_too_large", "Die Liste ist zu lang.")
    return values


# -- Detection -----------------------------------------------------------------


class CustomPatternRecognizer(EntityRecognizer):
    """Custom pattern recognizer (stage “Rules”), with timeout."""

    def __init__(self, pattern: Pattern) -> None:
        self.pattern_id = pattern.id
        self._compiled = regex.compile(pattern.regex)
        super().__init__(
            supported_entities=[pattern.entity_type],
            name="CustomPatternRecognizer",
            supported_language="de",
        )

    def load(self) -> None:  # pragma: no cover - nothing to load
        return None

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: Any = None,
        regex_flags: int | None = None,
    ) -> list[RecognizerResult]:
        del nlp_artifacts, regex_flags
        entity = self.supported_entities[0]
        if entities and entity not in entities:
            return []
        try:
            matches = [
                match
                for match in self._compiled.finditer(text, timeout=PATTERN_TIMEOUT_SECONDS)
                if match.end() > match.start()
            ]
        except TimeoutError as error:
            raise PatternTimeoutError("Ein eigenes Muster hat sein Zeitlimit gerissen.") from error
        return [
            RecognizerResult(
                entity_type=entity,
                start=match.start(),
                end=match.end(),
                score=1.0,
                recognition_metadata={
                    "source": "custom_pattern",
                    "pattern_id": self.pattern_id,
                    RecognizerResult.RECOGNIZER_NAME_KEY: self.name,
                },
            )
            for match in matches
        ]


def term_recognizers(terms: Iterable[Term]) -> tuple[EntityRecognizer, ...]:
    by_type: dict[str, list[str]] = {}
    for term in terms:
        by_type.setdefault(term.entity_type, []).append(term.text)
    return tuple(
        NormalizedDenyListRecognizer(
            supported_entity=entity,  # type: ignore[arg-type]
            deny_list=values,
            name="CustomTermRecognizer",
        )
        for entity, values in sorted(by_type.items())
    )


def list_recognizers(lists: Iterable[NameList]) -> tuple[EntityRecognizer, ...]:
    persons: list[str] = []
    organizations: list[str] = []
    for item in lists:
        (persons if item.kind == "persons" else organizations).extend(item.values)
    recognizers: list[EntityRecognizer] = []
    if persons:
        recognizers.append(
            NormalizedDenyListRecognizer(
                supported_entity="PERSON", deny_list=persons, name="NameListPersonRecognizer"
            )
        )
    if organizations:
        recognizers.append(
            NormalizedDenyListRecognizer(
                supported_entity="ORGANIZATION",
                deny_list=organizations,
                name="NameListOrganizationRecognizer",
            )
        )
    return tuple(recognizers)


def pattern_recognizers(patterns: Iterable[Pattern]) -> tuple[EntityRecognizer, ...]:
    return tuple(CustomPatternRecognizer(pattern) for pattern in patterns if pattern.enabled)


# -- Storage ------------------------------------------------------------------


class VocabularyStore:
    """Thread-safe; each change is written atomically encrypted."""

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
        self._document = self._load()
        self._recognizers: (
            tuple[tuple[EntityRecognizer, ...], tuple[EntityRecognizer, ...]] | None
        ) = None

    # -- Reading ---------------------------------------------------------------

    def lists(self) -> tuple[NameList, ...]:
        with self._lock:
            return self._document.lists

    def terms(self) -> tuple[Term, ...]:
        with self._lock:
            return self._document.terms

    def patterns(self) -> tuple[Pattern, ...]:
        with self._lock:
            return self._document.patterns

    def templates(self) -> tuple[Template, ...]:
        with self._lock:
            return self._document.templates

    def template(self, template_id: str) -> Template:
        with self._lock:
            for template in self._document.templates:
                if template.id == template_id:
                    return template
        raise VocabularyNotFoundError("Vorlage nicht gefunden.")

    def is_stale(self, item: NameList, *, now: datetime | None = None) -> bool:
        return (now or self._clock()) - item.updated_at > LIST_STALE_AFTER

    def recognizers(self) -> tuple[tuple[EntityRecognizer, ...], tuple[EntityRecognizer, ...]]:
        """(Names and terms, patterns) – built once per state.

        The deny-lists for names are combined into a single large regex.
        Building this for thousands of names is costly and must not be done
        for every document.
        """

        with self._lock:
            if self._recognizers is None:
                document = self._document
                self._recognizers = (
                    (*list_recognizers(document.lists), *term_recognizers(document.terms)),
                    pattern_recognizers(document.patterns),
                )
            return self._recognizers

    # -- Name lists --------------------------------------------------------

    def add_list(
        self,
        *,
        kind: ListKind,
        source_name: str,
        values: Sequence[str],
        source_modified_at: datetime | None = None,
    ) -> NameList:
        item = NameList(
            id=secrets.token_hex(8),
            kind=kind,
            source_name=_plain_name(source_name),
            values=tuple(values),
            imported_at=self._clock(),
            source_modified_at=source_modified_at,
        )
        with self._lock:
            if len(self._document.lists) >= MAX_LISTS:
                raise VocabularyValidationError("too_many_lists", "Zu viele Namenslisten.")
            self._replace(lists=(*self._document.lists, item))
        return item

    def refresh_list(
        self,
        list_id: str,
        *,
        source_name: str,
        values: Sequence[str],
        source_modified_at: datetime | None = None,
    ) -> NameList:
        with self._lock:
            current = self._list(list_id)
            item = current.model_copy(
                update={
                    "source_name": _plain_name(source_name),
                    "values": tuple(values),
                    "imported_at": self._clock(),
                    "source_modified_at": source_modified_at,
                }
            )
            self._replace(
                lists=tuple(
                    item if entry.id == list_id else entry for entry in self._document.lists
                )
            )
            return item

    def delete_list(self, list_id: str) -> None:
        with self._lock:
            self._list(list_id)
            self._replace(lists=tuple(item for item in self._document.lists if item.id != list_id))

    # -- Terms and patterns -------------------------------------------------

    def set_terms(self, terms: Sequence[TermIn]) -> tuple[Term, ...]:
        if len(terms) > MAX_TERMS:
            raise VocabularyValidationError("too_many_terms", "Zu viele Begriffe.")
        with self._lock:
            known = {
                (term.text.casefold(), term.entity_type): term.id for term in self._document.terms
            }
            result: list[Term] = []
            seen: set[tuple[str, str]] = set()
            for index, item in enumerate(terms):
                if len(normalize_registry_value(item.text)) < DEFAULT_MIN_LENGTH:
                    raise VocabularyValidationError(
                        "term_too_short",
                        f"Ein Begriff braucht mindestens {DEFAULT_MIN_LENGTH} Zeichen.",
                        index=index,
                    )
                key = (item.text.casefold(), item.entity_type)
                if key in seen:
                    continue
                seen.add(key)
                result.append(
                    Term(
                        id=known.get(key) or secrets.token_hex(6),
                        text=item.text,
                        entity_type=item.entity_type,
                    )
                )
            self._replace(terms=tuple(result))
            return self._document.terms

    def set_patterns(self, patterns: Sequence[PatternIn]) -> tuple[Pattern, ...]:
        if len(patterns) > MAX_PATTERNS:
            raise VocabularyValidationError("too_many_patterns", "Zu viele Muster.")
        validated = validate_patterns(patterns)
        with self._lock:
            self._replace(patterns=validated)
            return self._document.patterns

    # -- Templates ------------------------------------------------------------

    def save_template(
        self,
        *,
        title: str,
        terms: Sequence[Term],
        patterns: Sequence[Pattern],
        masking: MaskingDefaults | None,
    ) -> Template:
        template = Template(
            id=secrets.token_hex(8),
            title=" ".join(title.split()),
            terms=tuple(terms),
            patterns=tuple(patterns),
            masking=masking,
            created_at=self._clock(),
        )
        with self._lock:
            # Same title: replace the existing entry.
            others = tuple(
                item
                for item in self._document.templates
                if item.title.casefold() != template.title.casefold()
            )
            if len(others) >= MAX_TEMPLATES:
                raise VocabularyValidationError("too_many_templates", "Zu viele Vorlagen.")
            self._replace(templates=(*others, template))
        return template

    def apply_template(self, template: Template) -> None:
        """Replace current terms and patterns with those from the template."""

        with self._lock:
            self._replace(terms=template.terms, patterns=template.patterns)

    # -- internal --------------------------------------------------------------

    def _list(self, list_id: str) -> NameList:
        for item in self._document.lists:
            if item.id == list_id:
                return item
        raise VocabularyNotFoundError("Namensliste nicht gefunden.")

    def _replace(self, **changes: Any) -> None:
        document = _Document.model_validate(
            {
                **self._document.model_dump(),
                **{
                    key: [
                        item.model_dump() if isinstance(item, BaseModel) else item for item in value
                    ]
                    for key, value in changes.items()
                },
            }
        )
        self._persist(document)
        self._document = document
        self._recognizers = None

    def _load(self) -> _Document:
        try:
            payload = _read_private(self.path)
        except FileNotFoundError:
            return _Document()
        minimum = len(FORMAT_HEADER) + NONCE_SIZE + 16
        if len(payload) < minimum or not payload.startswith(FORMAT_HEADER):
            raise VocabularyError("Unbekanntes oder unvollstaendiges Format.")
        nonce_end = len(FORMAT_HEADER) + NONCE_SIZE
        try:
            plaintext = self._cipher.decrypt(
                payload[len(FORMAT_HEADER) : nonce_end],
                payload[nonce_end:],
                FORMAT_HEADER,
            )
            return _Document.model_validate(json.loads(plaintext))
        except InvalidTag as error:
            raise VocabularyError(
                "Namen & Begriffe konnten nicht authentifiziert werden."
            ) from error
        except (UnicodeError, json.JSONDecodeError, ValidationError) as error:
            raise VocabularyError("Namen & Begriffe sind ungueltig.") from error

    def _persist(self, document: _Document) -> None:
        plaintext = document.model_dump_json().encode("utf-8")
        nonce = os.urandom(NONCE_SIZE)
        payload = FORMAT_HEADER + nonce + self._cipher.encrypt(nonce, plaintext, FORMAT_HEADER)
        _atomic_private_write(self.path, payload)


def validate_patterns(patterns: Sequence[PatternIn]) -> tuple[Pattern, ...]:
    result: list[Pattern] = []
    used: set[str] = set()
    for index, item in enumerate(patterns):
        compile_pattern(item.regex, index=index)
        pattern_id = item.id if item.id and item.id not in used else secrets.token_hex(6)
        used.add(pattern_id)
        result.append(
            Pattern(
                id=pattern_id,
                name=item.name,
                regex=item.regex,
                entity_type=item.entity_type,
                enabled=item.enabled,
            )
        )
    return tuple(result)


def _plain_name(name: str) -> str:
    cleaned = Path(str(name).replace("\\", "/")).name.strip()
    return cleaned[:200] or "Liste"


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
            "wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
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
        raise VocabularyError("Namen & Begriffe konnten nicht gespeichert werden.") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


__all__ = [
    "LIST_STALE_AFTER",
    "CustomPatternRecognizer",
    "ListKind",
    "NameList",
    "Pattern",
    "PatternIn",
    "PatternTimeoutError",
    "Template",
    "Term",
    "TermIn",
    "TermType",
    "VocabularyError",
    "VocabularyNotFoundError",
    "VocabularyStore",
    "VocabularyValidationError",
    "compile_pattern",
    "parse_name_list",
    "validate_patterns",
]
