"""Versioned, fail-closed loaded processing profiles.

The YAML files consist of two tightly coupled layers:

* the declarative Presidio configuration (languages, engine, entities and
  thresholds), from which :meth:`ProfileConfig.to_presidio_analyzer_config`
  derives the native Analyzer configuration;
* application-specific rules not covered by Presidio for inputs, review,
  export and the encrypted fallback path.

All unknown fields are deliberately rejected. A typo in a security rule must
not cause Presidio to continue with a silent default.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    ValidationError,
    field_validator,
    model_validator,
)

from .resources import profiles_directory

PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ENTITY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
LANGUAGE_PATTERN = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$")
REGION_PATTERN = re.compile(r"^[A-Z]{2}$")
VERSION_PATTERN = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
PRESIDIO_ENTITY_ALIASES: Mapping[str, str] = {
    # The spec and its example profile use ORG, Presidio, while the processing
    # layer expects ORGANIZATION.
    "ORG": "ORGANIZATION",
}


class ProfileError(ValueError):
    """Base class for security-related profile errors."""


class ProfileLoadError(ProfileError):
    """A profile file is missing, not valid YAML, or violates the schema."""


class DuplicateProfileError(ProfileLoadError):
    """Multiple files declare the same profile ID."""


class ProfileRuleViolation(ProfileError):
    """An input or export violates the rules of a valid profile."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DocumentFormat(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    XLSX = "xlsx"
    CSV = "csv"
    PNG = "png"
    JPG = "jpg"
    TXT = "txt"
    MARKDOWN = "md"
    JSON = "json"
    EMAIL = "eml"


DOCUMENT_FORMAT_ALIASES: Mapping[str, DocumentFormat] = {
    "jpeg": DocumentFormat.JPG,
    "jpe": DocumentFormat.JPG,
    "markdown": DocumentFormat.MARKDOWN,
}


class OcrMode(StrEnum):
    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"


class ExecutionMode(StrEnum):
    FAST = "fast"
    BALANCED = "balanced"
    THOROUGH = "thorough"


class NlpEngine(StrEnum):
    GLINER = "gliner"
    SPACY = "spacy"
    TRANSFORMERS = "transformers"


class GpuMode(StrEnum):
    AUTO = "auto"
    CPU = "cpu"
    PREFER_GPU = "prefer_gpu"


class RefreshMode(StrEnum):
    DAILY = "daily"
    ON_START = "on_start"
    MANUAL = "manual"


class ProcessingMode(StrEnum):
    TOKEN_DETERMINISTIC = "token_deterministic"
    SURROGATE = "surrogate"
    ENCRYPT = "encrypt"
    HASH = "hash"
    PLACEHOLDER = "placeholder"
    MASK = "mask"
    REMOVE = "remove"


REVERSIBLE_PROCESSING_MODES = frozenset(
    {
        ProcessingMode.TOKEN_DETERMINISTIC,
        ProcessingMode.SURROGATE,
        ProcessingMode.ENCRYPT,
    }
)


class PolicyMode(StrEnum):
    INHERIT = "inherit"
    TOKEN_DETERMINISTIC = "token_deterministic"
    SURROGATE = "surrogate"
    ENCRYPT = "encrypt"
    HASH = "hash"
    PLACEHOLDER = "placeholder"
    MASK = "mask"
    REMOVE = "remove"


REVERSIBLE_POLICY_MODES = frozenset(
    {
        PolicyMode.INHERIT,
        PolicyMode.TOKEN_DETERMINISTIC,
        PolicyMode.SURROGATE,
        PolicyMode.ENCRYPT,
    }
)


class AuditLogMode(StrEnum):
    MINIMIZED = "minimized"
    DISABLED = "disabled"


class StrictModel(BaseModel):
    """Common Pydantic settings for the profile schema."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


PositiveScore = Annotated[float, Field(gt=0.0, le=1.0, allow_inf_nan=False)]


def _unique(values: Iterable[Any], field_name: str) -> tuple[Any, ...]:
    result = tuple(values)
    if len(result) != len(set(result)):
        raise ValueError(f"{field_name} darf keine Duplikate enthalten")
    return result


def _normalize_entity(value: str) -> str:
    entity = value.strip().upper()
    if not ENTITY_PATTERN.fullmatch(entity):
        raise ValueError(f"ungueltiger Entitaetstyp: {value!r}")
    return entity


def runtime_entity_type(value: str) -> str:
    """Normalize a profile type to the Presidio runtime name."""

    entity = _normalize_entity(value)
    return PRESIDIO_ENTITY_ALIASES.get(entity, entity)


def _normalize_entities(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    return _unique((_normalize_entity(value) for value in values), field_name)


def _validate_relative_resource_path(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or ".." in path.parts
        or any(":" in part for part in path.parts)
    ):
        raise ValueError("Ressourcenpfade muessen relativ sein und duerfen kein '..' enthalten")
    return path.as_posix()


class InputRules(StrictModel):
    allow: tuple[DocumentFormat, ...] = Field(min_length=1)
    max_file_mb: Annotated[float, Field(gt=0.0, le=2048.0, allow_inf_nan=False)]

    @field_validator("allow")
    @classmethod
    def unique_formats(cls, values: tuple[DocumentFormat, ...]) -> tuple[DocumentFormat, ...]:
        return _unique(values, "input.allow")

    @property
    def max_file_bytes(self) -> int:
        """Binary MiB limit for Content-Length and local files."""

        return int(self.max_file_mb * 1024 * 1024)


class MasterDataConfig(StrictModel):
    enabled: bool
    persons_file: str | None = None
    orgs_file: str | None = None
    min_length: Annotated[int, Field(ge=2, le=100)] = 3
    refresh: RefreshMode = RefreshMode.DAILY

    @field_validator("persons_file", "orgs_file")
    @classmethod
    def safe_resource_path(cls, value: str | None) -> str | None:
        return _validate_relative_resource_path(value)

    @model_validator(mode="after")
    def enabled_profiles_have_a_source(self) -> MasterDataConfig:
        if self.enabled and not (self.persons_file and self.orgs_file):
            raise ValueError("Aktivierte Stammdaten brauchen persons_file und orgs_file")
        return self


class PipelineConfig(StrictModel):
    ocr: OcrMode
    ocr_languages: tuple[str, ...] = ()
    structured_data: Literal["presidio_structured", "direct"] | None = None

    @field_validator("ocr_languages")
    @classmethod
    def normalize_ocr_languages(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip().lower() for value in values)
        for value in normalized:
            if not re.fullmatch(r"[a-z]{3}", value):
                raise ValueError("OCR-Sprachen muessen dreistellige Tesseract-Codes sein")
        return _unique(normalized, "pipeline.ocr_languages")

    @model_validator(mode="after")
    def active_ocr_has_languages(self) -> PipelineConfig:
        if self.ocr is not OcrMode.NEVER and not self.ocr_languages:
            raise ValueError("Aktives OCR braucht mindestens eine OCR-Sprache")
        return self


class ExecutionConfig(StrictModel):
    mode: ExecutionMode
    nlp_engine: NlpEngine
    spacy_fallback: str | None = None
    gpu: GpuMode
    cpu_workers: Literal["auto"] | Annotated[int, Field(ge=1, le=64)]

    @model_validator(mode="after")
    def gliner_has_offline_fallback(self) -> ExecutionConfig:
        if self.nlp_engine is NlpEngine.GLINER and not self.spacy_fallback:
            raise ValueError("GLiNER-Profile brauchen einen lokalen spaCy-Fallback")
        return self


class EntityConfig(StrictModel):
    built_in: tuple[str, ...] = ()
    custom: tuple[str, ...] = ()

    @field_validator("built_in")
    @classmethod
    def normalize_built_ins(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_entities(values, "entities.built_in")

    @field_validator("custom")
    @classmethod
    def normalize_custom(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_entities(values, "entities.custom")

    @model_validator(mode="after")
    def entities_are_nonempty_and_disjoint(self) -> EntityConfig:
        if not (self.built_in or self.custom):
            raise ValueError("Ein Profil braucht mindestens einen Entitaetstyp")
        overlap = {runtime_entity_type(entity) for entity in self.built_in} & {
            runtime_entity_type(entity) for entity in self.custom
        }
        if overlap:
            raise ValueError(
                "Entitaeten duerfen nicht zugleich built_in und custom sein: "
                + ", ".join(sorted(overlap))
            )
        return self

    @property
    def all(self) -> tuple[str, ...]:
        return self.built_in + self.custom


class ThresholdRule(StrictModel):
    enabled: bool = True
    auto_accept: PositiveScore | None = None
    needs_review: PositiveScore | None = None

    @model_validator(mode="after")
    def review_threshold_does_not_exceed_acceptance(self) -> ThresholdRule:
        if (
            self.auto_accept is not None
            and self.needs_review is not None
            and self.needs_review > self.auto_accept
        ):
            raise ValueError("needs_review darf nicht ueber auto_accept liegen")
        return self


class ThresholdConfig(RootModel[dict[str, ThresholdRule]]):
    """Dynamic thresholds per entity plus mandatory default."""

    model_config = ConfigDict(frozen=True)

    @model_validator(mode="before")
    @classmethod
    def normalize_keys(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        normalized: dict[str, Any] = {}
        for raw_key, rule in value.items():
            key = str(raw_key).strip()
            normalized_key = "default" if key.lower() == "default" else _normalize_entity(key)
            if normalized_key in normalized:
                raise ValueError(f"Doppelte Schwellenregel fuer {normalized_key}")
            normalized[normalized_key] = rule
        return normalized

    @model_validator(mode="after")
    def validate_default(self) -> ThresholdConfig:
        default = self.root.get("default")
        if default is None:
            raise ValueError("thresholds.default fehlt")
        if not default.enabled:
            raise ValueError("thresholds.default darf nicht deaktiviert sein")
        if default.auto_accept is None or default.needs_review is None:
            raise ValueError("thresholds.default braucht auto_accept und needs_review")
        return self

    @property
    def default(self) -> ThresholdRule:
        return self.root["default"]

    def for_entity(self, entity_type: str) -> ThresholdRule:
        requested = runtime_entity_type(entity_type)
        for configured_entity, rule in self.root.items():
            if (
                configured_entity != "default"
                and runtime_entity_type(configured_entity) == requested
            ):
                return rule
        return self.default


class SuspectCheckConfig(StrictModel):
    enabled: bool
    pattern: Literal["capitalized_bigram"] | None = None
    stopwords_file: str | None = None

    @field_validator("stopwords_file")
    @classmethod
    def safe_stopwords_path(cls, value: str | None) -> str | None:
        return _validate_relative_resource_path(value)

    @model_validator(mode="after")
    def enabled_check_is_configured(self) -> SuspectCheckConfig:
        if self.enabled and not (self.pattern and self.stopwords_file):
            raise ValueError("Aktivierte Verdachtspruefung braucht pattern und stopwords_file")
        return self


class ProtectionOverrides(StrictModel):
    keep_plain: tuple[str, ...] = ()
    force_handle: tuple[str, ...] = ()

    @field_validator("keep_plain")
    @classmethod
    def normalize_keep_plain(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_entities(values, "protection.overrides.keep_plain")

    @field_validator("force_handle")
    @classmethod
    def normalize_force_handle(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_entities(values, "protection.overrides.force_handle")

    @model_validator(mode="after")
    def overrides_do_not_conflict(self) -> ProtectionOverrides:
        overlap = set(self.keep_plain) & set(self.force_handle)
        if overlap:
            raise ValueError(
                "keep_plain und force_handle widersprechen sich fuer: " + ", ".join(sorted(overlap))
            )
        return self


class ProtectionConfig(StrictModel):
    level: Annotated[int, Field(ge=1, le=3)]
    level_locked: bool
    overrides: ProtectionOverrides = Field(default_factory=ProtectionOverrides)


class ModeConfig(StrictModel):
    default: ProcessingMode
    user_selectable: bool
    allowed: tuple[ProcessingMode, ...] = Field(min_length=1)
    confirm_irreversible: bool

    @field_validator("allowed")
    @classmethod
    def unique_modes(cls, values: tuple[ProcessingMode, ...]) -> tuple[ProcessingMode, ...]:
        return _unique(values, "mode.allowed")

    @model_validator(mode="after")
    def default_mode_is_allowed(self) -> ModeConfig:
        if self.default not in self.allowed:
            raise ValueError("mode.default muss in mode.allowed enthalten sein")
        if not self.user_selectable and self.allowed != (self.default,):
            raise ValueError(
                "Bei user_selectable=false darf mode.allowed nur mode.default enthalten"
            )
        return self


class ResidualRiskConfig(StrictModel):
    enabled: bool
    warn_at_classes: Annotated[int, Field(ge=1, le=100)] = 2


class PolicyConfig(RootModel[dict[str, PolicyMode]]):
    model_config = ConfigDict(frozen=True)

    @model_validator(mode="before")
    @classmethod
    def normalize_keys(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        normalized: dict[str, Any] = {}
        for raw_key, mode in value.items():
            entity = _normalize_entity(str(raw_key))
            if entity in normalized:
                raise ValueError(f"Doppelte Policy fuer {entity}")
            normalized[entity] = mode
        return normalized


class ReviewConfig(StrictModel):
    required_entities: tuple[str, ...]
    block_export_on_unreviewed: bool

    @field_validator("required_entities")
    @classmethod
    def normalize_required_entities(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_entities(values, "review.required_entities")

    @model_validator(mode="after")
    def blocking_review_has_required_entities(self) -> ReviewConfig:
        if self.block_export_on_unreviewed and not self.required_entities:
            raise ValueError("block_export_on_unreviewed braucht mindestens eine required_entity")
        return self


class VaultConfig(StrictModel):
    reversible: bool
    persist_mapping: bool

    @model_validator(mode="after")
    def reversible_mapping_is_consistent(self) -> VaultConfig:
        if self.reversible != self.persist_mapping:
            raise ValueError(
                "vault.reversible und vault.persist_mapping muessen gemeinsam gesetzt sein"
            )
        return self


class ExportConfig(StrictModel):
    preserve_original: Literal[True]
    write_audit_log: AuditLogMode
    pdf_render_check: bool


class ProfileConfig(StrictModel):
    id: str
    title: str = Field(min_length=1, max_length=120)
    version: str
    languages: tuple[str, ...] = Field(min_length=1)
    input: InputRules
    master_data: MasterDataConfig
    pipeline: PipelineConfig
    execution: ExecutionConfig
    entities: EntityConfig
    phone_regions: tuple[str, ...] = ()
    thresholds: ThresholdConfig
    suspect_check: SuspectCheckConfig
    protection: ProtectionConfig
    mode: ModeConfig
    residual_risk_check: ResidualRiskConfig
    policies: PolicyConfig
    review: ReviewConfig
    vault: VaultConfig
    export: ExportConfig

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not PROFILE_ID_PATTERN.fullmatch(normalized):
            raise ValueError("Profil-ID muss ein kleingeschriebener Bindestrich-Slug sein")
        return normalized

    @field_validator("version")
    @classmethod
    def semantic_version(cls, value: str) -> str:
        if not VERSION_PATTERN.fullmatch(value):
            raise ValueError("version muss eine semantische Version wie 3.0.0 sein")
        return value

    @field_validator("languages")
    @classmethod
    def valid_languages(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip().lower() for value in values)
        for value in normalized:
            if not LANGUAGE_PATTERN.fullmatch(value):
                raise ValueError(f"ungueltiger Sprachcode: {value!r}")
        return _unique(normalized, "languages")

    @field_validator("phone_regions")
    @classmethod
    def valid_phone_regions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip().upper() for value in values)
        for value in normalized:
            if not REGION_PATTERN.fullmatch(value):
                raise ValueError(f"ungueltige Telefonregion: {value!r}")
        return _unique(normalized, "phone_regions")

    @model_validator(mode="after")
    def cross_check_app_rules(self) -> ProfileConfig:
        _assert_runtime_unique(self.entities.all, "entities")
        _assert_runtime_unique(
            (entity for entity in self.thresholds.root if entity != "default"),
            "thresholds",
        )
        _assert_runtime_unique(self.policies.root, "policies")
        _assert_runtime_unique(
            self.review.required_entities,
            "review.required_entities",
        )
        _assert_runtime_unique(
            self.protection.overrides.keep_plain,
            "protection.overrides.keep_plain",
        )
        _assert_runtime_unique(
            self.protection.overrides.force_handle,
            "protection.overrides.force_handle",
        )
        configured_entities = {runtime_entity_type(entity) for entity in self.entities.all}
        if "PHONE_NUMBER" in configured_entities and not self.phone_regions:
            raise ValueError("PHONE_NUMBER braucht mindestens eine phone_region")
        keep_plain_entities = {
            runtime_entity_type(entity) for entity in self.protection.overrides.keep_plain
        }
        force_handle_entities = {
            runtime_entity_type(entity) for entity in self.protection.overrides.force_handle
        }
        conflicting_overrides = keep_plain_entities & force_handle_entities
        if conflicting_overrides:
            raise ValueError(
                "keep_plain und force_handle widersprechen sich fuer: "
                + ", ".join(sorted(conflicting_overrides))
            )
        missing_overrides = (keep_plain_entities | force_handle_entities) - configured_entities
        if missing_overrides:
            raise ValueError(
                "Overrides verweisen auf nicht konfigurierte Entitaeten: "
                + ", ".join(sorted(missing_overrides))
            )
        missing_required = {
            runtime_entity_type(entity) for entity in self.review.required_entities
        } - configured_entities
        if missing_required:
            raise ValueError(
                "Review-Pflicht verweist auf nicht konfigurierte Entitaeten: "
                + ", ".join(sorted(missing_required))
            )

        missing_policies = {
            runtime_entity_type(entity) for entity in self.policies.root
        } - configured_entities
        if missing_policies:
            raise ValueError(
                "Policies verweisen auf nicht konfigurierte Entitaeten: "
                + ", ".join(sorted(missing_policies))
            )

        if DocumentFormat.PDF in self.input.allow and not self.export.pdf_render_check:
            raise ValueError("PDF-Profile muessen export.pdf_render_check aktivieren")

        if self.id == "llm-upload" or self.id.startswith("llm-upload-"):
            if not self.vault.reversible:
                raise ValueError("LLM-Upload-Profile muessen vault.reversible aktivieren")
            if self.mode.default not in REVERSIBLE_PROCESSING_MODES:
                raise ValueError("LLM-Upload-Profile brauchen einen rueckholbaren Standardmodus")
            irreversible_choices = set(self.mode.allowed) - REVERSIBLE_PROCESSING_MODES
            if irreversible_choices:
                raise ValueError(
                    "LLM-Upload darf keine unumkehrbaren Modi anbieten: "
                    + ", ".join(sorted(mode.value for mode in irreversible_choices))
                )
            irreversible_policies = {
                entity: policy
                for entity, policy in self.policies.root.items()
                if policy not in REVERSIBLE_POLICY_MODES
            }
            if irreversible_policies:
                formatted = ", ".join(
                    f"{entity}={policy.value}"
                    for entity, policy in sorted(irreversible_policies.items())
                )
                raise ValueError(
                    "LLM-Upload darf keine unumkehrbaren Entity-Policies setzen: " + formatted
                )
        if not self.vault.reversible:
            if ProcessingMode.SURROGATE in self.mode.allowed:
                raise ValueError("Decknamen brauchen ein Profil mit vault.reversible=true")
            surrogate_policies = {
                entity
                for entity, policy in self.policies.root.items()
                if policy is PolicyMode.SURROGATE
            }
            if surrogate_policies:
                raise ValueError(
                    "Decknamen-Policies brauchen vault.reversible=true: "
                    + ", ".join(sorted(surrogate_policies))
                )
        return self

    @property
    def max_file_bytes(self) -> int:
        return self.input.max_file_bytes

    @property
    def presidio_entities(self) -> tuple[str, ...]:
        """Entity filter for ``AnalyzerEngine.analyze``."""

        return tuple(dict.fromkeys(runtime_entity_type(entity) for entity in self.entities.all))

    @property
    def required_review_entities(self) -> tuple[str, ...]:
        """Review obligations with runtime entity names."""

        return tuple(
            dict.fromkeys(runtime_entity_type(entity) for entity in self.review.required_entities)
        )

    def to_presidio_analyzer_config(self) -> dict[str, Any]:
        """Return the native no-code portion for ``AnalyzerEngineProvider``.

        The lowest review threshold is used as the analyzer lower bound so the later app layer does
        not miss matches. Entity-specific review and auto-accept thresholds remain in this layer.
        """

        review_thresholds = [
            rule.needs_review
            for rule in self.thresholds.root.values()
            if rule.enabled and rule.needs_review is not None
        ]
        return {
            "supported_languages": list(self.languages),
            "default_score_threshold": min(review_thresholds),
        }

    def validate_upload(self, filename: str, size_bytes: int) -> DocumentFormat:
        """Check file type and size before reading document content."""

        document_format = document_format_from_filename(filename)
        if document_format not in self.input.allow:
            allowed = ", ".join(item.value for item in self.input.allow)
            raise ProfileRuleViolation(
                "format_not_allowed",
                f"Profil {self.id!r} erlaubt {document_format.value!r} nicht (erlaubt: {allowed}).",
            )
        if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 0:
            raise ProfileRuleViolation(
                "invalid_file_size",
                "Dateigroesse muss eine nicht-negative Ganzzahl in Bytes sein.",
            )
        if size_bytes > self.max_file_bytes:
            raise ProfileRuleViolation(
                "file_too_large",
                f"Datei ist groesser als die Profilgrenze von {self.input.max_file_mb:g} MiB.",
            )
        return document_format

    def validate_mode(
        self,
        requested_mode: ProcessingMode | str | None = None,
    ) -> ProcessingMode:
        """Resolve the apply mode and enforce profile deviations fail-closed."""

        if requested_mode is None:
            return self.mode.default
        try:
            resolved = ProcessingMode(requested_mode)
        except (TypeError, ValueError) as error:
            raise ProfileRuleViolation(
                "invalid_processing_mode",
                f"Unbekannter Verarbeitungsmodus: {requested_mode!r}.",
            ) from error
        if not self.mode.user_selectable and resolved != self.mode.default:
            raise ProfileRuleViolation(
                "processing_mode_locked",
                f"Profil {self.id!r} sperrt den Modus auf {self.mode.default.value!r}.",
            )
        if resolved not in self.mode.allowed:
            raise ProfileRuleViolation(
                "processing_mode_not_allowed",
                f"Modus {resolved.value!r} ist fuer Profil {self.id!r} nicht erlaubt.",
            )
        if (
            self.id == "llm-upload" or self.id.startswith("llm-upload-")
        ) and resolved not in REVERSIBLE_PROCESSING_MODES:
            raise ProfileRuleViolation(
                "reversible_mode_required",
                "Das LLM-Upload-Profil erlaubt ausschliesslich rueckholbare Modi.",
            )
        return resolved

    def apply_defaults(
        self,
        requested_mode: ProcessingMode | str | None = None,
    ) -> dict[str, Any]:
        """Return values compatible with ``ProcessingRequest.from_dict``."""

        resolved_mode = self.validate_mode(requested_mode)
        return {
            "mode": resolved_mode.value,
            "protection_level": self.protection.level,
            "overrides": {
                "keep_plain": [
                    runtime_entity_type(entity) for entity in self.protection.overrides.keep_plain
                ],
                "force_handle": [
                    runtime_entity_type(entity) for entity in self.protection.overrides.force_handle
                ],
            },
            # ``inherit`` means the policy is omitted in ProcessingRequest.
            "policies": {
                runtime_entity_type(entity): policy.value
                for entity, policy in self.policies.root.items()
                if policy is not PolicyMode.INHERIT
            },
            "vault_reversible": self.vault.reversible,
        }

    def export_blockers(self, unreviewed_entities: Iterable[str]) -> tuple[str, ...]:
        """Return open mandatory entity types in a deterministic order."""

        if not self.review.block_export_on_unreviewed:
            return ()
        unreviewed = {runtime_entity_type(value) for value in unreviewed_entities}
        return tuple(entity for entity in self.required_review_entities if entity in unreviewed)

    def assert_export_allowed(self, unreviewed_entities: Iterable[str]) -> None:
        """Block export when mandatory review items are still open."""

        blockers = self.export_blockers(unreviewed_entities)
        if blockers:
            raise ProfileRuleViolation(
                "required_review_pending",
                "Export gesperrt; Review ausstehend fuer: " + ", ".join(blockers),
            )


# Short, intuitive name for callers and future API models.
Profile = ProfileConfig


def _assert_runtime_unique(values: Iterable[str], field_name: str) -> None:
    normalized = [runtime_entity_type(value) for value in values]
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} enthaelt nach ORG-Normalisierung Duplikate")


def document_format_from_filename(filename: str) -> DocumentFormat:
    """Determine an allowed format from the file extension only."""

    if not isinstance(filename, str) or not filename.strip():
        raise ProfileRuleViolation("missing_filename", "Dateiname fehlt.")
    suffix = Path(filename.strip()).suffix.lower().removeprefix(".")
    if not suffix:
        raise ProfileRuleViolation(
            "missing_file_extension",
            "Dateiname hat keine erkennbare Endung.",
        )
    try:
        return DOCUMENT_FORMAT_ALIASES[suffix]
    except KeyError:
        pass
    try:
        return DocumentFormat(suffix)
    except ValueError as error:
        raise ProfileRuleViolation(
            "unsupported_file_format",
            f"Unbekanntes Dokumentformat: {suffix!r}.",
        ) from error


def default_profiles_directory() -> Path:
    """Profile directory from the signed bundle or repository."""

    return profiles_directory()


def load_profile(path: str | Path) -> ProfileConfig:
    """Load a single YAML file and provide clear profile errors."""

    profile_path = Path(path)
    if profile_path.suffix.lower() not in {".yaml", ".yml"}:
        raise ProfileLoadError(f"Profildatei muss .yaml oder .yml sein: {profile_path}")
    if not profile_path.is_file():
        raise ProfileLoadError(f"Profildatei nicht gefunden: {profile_path}")
    try:
        with profile_path.open("r", encoding="utf-8") as stream:
            raw_profile = yaml.safe_load(stream)
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise ProfileLoadError(f"Profildatei kann nicht gelesen werden: {profile_path}") from error
    if not isinstance(raw_profile, Mapping):
        raise ProfileLoadError(f"Profilwurzel muss ein YAML-Objekt sein: {profile_path}")
    try:
        return ProfileConfig.model_validate(raw_profile)
    except ValidationError as error:
        raise ProfileLoadError(f"Ungueltiges Profil {profile_path}: {error}") from error


def load_profiles(directory: str | Path | None = None) -> tuple[ProfileConfig, ...]:
    """Load all profiles atomically; a failure aborts the whole directory."""

    profile_directory = Path(directory) if directory is not None else default_profiles_directory()
    if not profile_directory.is_dir():
        raise ProfileLoadError(f"Profilordner nicht gefunden: {profile_directory}")
    paths = sorted(
        (
            path
            for path in profile_directory.iterdir()
            if path.is_file() and path.suffix.lower() in {".yaml", ".yml"}
        ),
        key=lambda path: path.name.casefold(),
    )
    if not paths:
        raise ProfileLoadError(f"Keine YAML-Profile in {profile_directory}")

    profiles: list[ProfileConfig] = []
    source_by_id: dict[str, Path] = {}
    for path in paths:
        profile = load_profile(path)
        previous = source_by_id.get(profile.id)
        if previous is not None:
            raise DuplicateProfileError(
                f"Doppelte Profil-ID {profile.id!r}: {previous.name}, {path.name}"
            )
        source_by_id[profile.id] = path
        profiles.append(profile)
    return tuple(profiles)


def profile_by_id(
    profile_id: str,
    profiles: Iterable[ProfileConfig],
) -> ProfileConfig:
    """Find a profile without unsafe fallback to any preset."""

    normalized = profile_id.strip().lower()
    for profile in profiles:
        if profile.id == normalized:
            return profile
    raise ProfileLoadError(f"Unbekannte Profil-ID: {profile_id!r}")
