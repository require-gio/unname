"""UI settings with schema version (spec §4.1, §4.3).

All data from the settings dialog that the sidecar needs lives in
``sidecar-state/settings.json``. The file contains no personal data –
name lists, terms and patterns are stored encrypted elsewhere – and it
gets mode 0600 permissions.

Each version includes ``schema_version``. Older versions are migrated
step-by-step on read; a newer version (written by a later app release) is
rejected. Silent reset to defaults could otherwise, e.g., extend a short
retention period.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .catalog import CATEGORY_IDS, STYLES, default_category_ids, locked_category_ids

SCHEMA_VERSION: Final = 3


class SettingsError(RuntimeError):
    """Failed to read or write settings safely."""


class SettingsValidationError(SettingsError, ValueError):
    """A requested change violates the schema."""


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GeneralSettings(_Section):
    language: Literal["de", "en"] = "de"
    theme: Literal["system", "light", "dark"] = "system"


class MaskingDefaults(_Section):
    """Default masking for new entries.

    Without ``anonymize``: derived and never stored.
    """

    method: Literal["reversible", "irreversible"] = "reversible"
    style: Literal["codes", "surrogates", "placeholder", "mask", "remove"] = "codes"
    categories: tuple[str, ...] = Field(default_factory=default_category_ids)

    @field_validator("categories")
    @classmethod
    def _known(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = sorted(set(value) - set(CATEGORY_IDS))
        if unknown:
            raise ValueError("Unbekannte Kategorie: " + ", ".join(unknown))
        chosen = set(value) | set(locked_category_ids())
        return tuple(item for item in CATEGORY_IDS if item in chosen)

    @model_validator(mode="after")
    def _style_fits_method(self) -> MaskingDefaults:
        if self.style not in STYLES[self.method]:
            raise ValueError(f"Stil {self.style!r} passt nicht zur Methode {self.method!r}.")
        return self


class DocumentLanguageSettings(_Section):
    """Document languages and countries.

    ``languages``: languages to check documents in; the first is the default
    for sections without a detectable language. ``per_paragraph``:
    detect language per paragraph instead of once per document.
    ``countries``: numeric IDs and IDs of countries to recognize –
    independent of language. ``placeholders_in_document_language``:
    placeholders and invented names in the paragraph's language instead of the app's.
    """

    languages: tuple[Literal["de", "en"], ...] = Field(default=("de", "en"), min_length=1)
    per_paragraph: bool = True
    countries: tuple[Literal["DE", "AT"], ...] = Field(default=("DE", "AT"), min_length=1)
    placeholders_in_document_language: bool = True

    @field_validator("languages")
    @classmethod
    def _languages_in_order(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(item for item in ("de", "en") if item in set(value))

    @field_validator("countries")
    @classmethod
    def _countries_in_order(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(item for item in ("DE", "AT") if item in set(value))


class SecondOpinionSettings(_Section):
    """Second opinion (local classifier).

    Active only if the model is installed (``/models``: ``classifier``).
    """

    enabled: bool = True
    #: ``protect``: the model decides only towards protection. ``protect_and_keep``:
    #: additionally “keep” above ``keep_threshold``. ``suggest``: it only suggests,
    #: final decision is made manually.
    policy: Literal["protect", "protect_and_keep", "suggest"] = "protect"
    keep_threshold: float = Field(default=0.95, ge=0.5, le=1.0)


class NamesTermsSettings(_Section):
    template: str | None = Field(default=None, max_length=120)


class HistorySettings(_Section):
    #: Auto-delete after 7/30/90 days or never (``None``).
    retention_days: Literal[7, 30, 90] | None = 30


class VaultSettings(_Section):
    #: Delete unused entries from translation table after 6/12 months or never (``None``).
    unused_retention_months: Literal[6, 12] | None = 12


class TechnicalSettings(_Section):
    developer_mode: bool = False


class AppSettings(_Section):
    schema_version: Literal[3] = SCHEMA_VERSION
    general: GeneralSettings = Field(default_factory=GeneralSettings)
    masking: MaskingDefaults = Field(default_factory=MaskingDefaults)
    document_languages: DocumentLanguageSettings = Field(default_factory=DocumentLanguageSettings)
    second_opinion: SecondOpinionSettings = Field(default_factory=SecondOpinionSettings)
    names_terms: NamesTermsSettings = Field(default_factory=NamesTermsSettings)
    history: HistorySettings = Field(default_factory=HistorySettings)
    vault: VaultSettings = Field(default_factory=VaultSettings)
    technical: TechnicalSettings = Field(default_factory=TechnicalSettings)


#: Migrations steps ``from version n`` → version ``n + 1``. Version 0 stands for a file without
#: ``schema_version`` (pre-first version).
def _to_v2(raw: dict[str, Any]) -> dict[str, Any]:
    """Document languages were previously read-only; version 1 reflects that.
    Version 2 introduces its own settings (both DE and AT)."""

    migrated = {key: value for key, value in raw.items() if key != "document_languages"}
    return {**migrated, "schema_version": 2}


def _to_v3(raw: dict[str, Any]) -> dict[str, Any]:
    """Second opinion was previously read-only with a locked switch; version 2 reflects that.
    Version 3 introduces its own settings (enabled, protection-only mode)."""

    migrated = {key: value for key, value in raw.items() if key != "second_opinion"}
    return {**migrated, "schema_version": 3}


_MIGRATIONS: Final[Mapping[int, Callable[[dict[str, Any]], dict[str, Any]]]] = {
    0: lambda raw: {**raw, "schema_version": 1},
    1: _to_v2,
    2: _to_v3,
}


def migrate(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Incrementally upgrades a stored version to ``SCHEMA_VERSION``."""

    data = dict(raw)
    version = data.get("schema_version", 0)
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise SettingsError("Ungueltige Schema-Version der Einstellungen.")
    if version > SCHEMA_VERSION:
        raise SettingsError(
            "Die Einstellungen stammen von einer neueren App-Version und werden nicht veraendert."
        )
    while version < SCHEMA_VERSION:
        data = _MIGRATIONS[version](data)
        version = data["schema_version"]
    return data


class SettingsStore:
    """Thread-safe storage with atomic writes."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._current: AppSettings | None = None

    def get(self) -> AppSettings:
        with self._lock:
            if self._current is None:
                self._current = self._load()
            return self._current

    def update(self, changes: Mapping[str, Any]) -> AppSettings:
        """Applies the specified sections; unspecified sections remain unchanged.

        Within a section, the listed fields are replaced. The result is validated as a whole
        before any write occurs.
        """

        if not isinstance(changes, Mapping):
            raise SettingsValidationError("Die Einstellungen muessen ein Objekt sein.")
        with self._lock:
            merged = self.get().model_dump(mode="json")
            for section, value in changes.items():
                if section == "schema_version":
                    if value != SCHEMA_VERSION:
                        raise SettingsValidationError("Falsche Schema-Version.")
                    continue
                if section not in merged:
                    raise SettingsValidationError(f"Unbekannter Bereich: {section}.")
                if not isinstance(value, Mapping):
                    raise SettingsValidationError(f"Bereich {section} muss ein Objekt sein.")
                merged[section] = {**merged[section], **value}
            try:
                updated = AppSettings.model_validate(merged)
            except ValidationError as error:
                raise SettingsValidationError(_public_validation_error(error)) from error
            self._write(updated)
            self._current = updated
            return updated

    def _load(self) -> AppSettings:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return AppSettings()
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise SettingsError("Die Einstellungen konnten nicht gelesen werden.") from error
        if not isinstance(raw, Mapping):
            raise SettingsError("Die Einstellungsdatei ist kein Objekt.")
        try:
            return AppSettings.model_validate(migrate(raw))
        except ValidationError as error:
            raise SettingsError("Die Einstellungsdatei verletzt das Schema.") from error

    def _write(self, settings: AppSettings) -> None:
        payload = json.dumps(settings.model_dump(mode="json"), ensure_ascii=False, indent=2)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                if os.name == "posix":
                    os.chmod(temporary, 0o600)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
        except OSError as error:
            raise SettingsError("Die Einstellungen konnten nicht gespeichert werden.") from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def _public_validation_error(error: ValidationError) -> str:
    # Include only field paths and error type, omit submitted values.
    parts = [
        ".".join(str(item) for item in detail["loc"]) + f" ({detail['type']})"
        for detail in error.errors()
    ]
    return "Ungueltige Einstellungen: " + "; ".join(parts)


__all__ = [
    "SCHEMA_VERSION",
    "AppSettings",
    "SettingsError",
    "SettingsStore",
    "SettingsValidationError",
    "migrate",
]
