"""Processing detected text spans according to spec §7.

The module is deliberately independent of FastAPI and job management. It
receives the original source text and a completed ``DetectionResult``
and returns a full, content-free processing log alongside the cleaned text.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from types import MappingProxyType
from typing import Final

from presidio_anonymizer.operators import Decrypt, Encrypt, Hash

from .catalog import LANGUAGES, placeholder_for
from .detection import (
    DetectionResult,
    DuplicateReviewCapacityError,
    Finding,
    FindingSource,
    SpanReplacement,
    replace_back_to_front,
)
from .detection.pipeline import compact_value, iter_value_occurrences
from .residual_risk import (
    ResidualRiskAnalyzer,
    ResidualRiskClass,
    ResidualRiskResult,
    ResidualRiskStatus,
)
from .vault import (
    PendingMapping,
    TokenVault,
    canonical_rank,
    normalize_entity_type,
    normalize_value,
)


class ProcessingError(RuntimeError):
    """Base class for errors that can be passed to the API."""


class ProcessingValidationError(ProcessingError, ValueError):
    """The selected processing configuration is contradictory."""


class UnknownEntityTypeError(ProcessingValidationError):
    """A finding has no assigned protection level.

    New recognizers must not emit clear text unnoticed. Each new entity type
    requires an explicit classification first.
    """


class ProcessingMode(StrEnum):
    TOKEN_DETERMINISTIC = "token_deterministic"
    SURROGATE = "surrogate"
    ENCRYPT = "encrypt"
    HASH = "hash"
    PLACEHOLDER = "placeholder"
    MASK = "mask"
    REMOVE = "remove"

    @property
    def anonymization_eligible(self) -> bool:
        """Only these three modes may contribute to “anonymized”."""

        return self in {
            ProcessingMode.PLACEHOLDER,
            ProcessingMode.MASK,
            ProcessingMode.REMOVE,
        }

    @property
    def pseudonymizing(self) -> bool:
        # Hash remains linkable despite lacking a restore path (spec §7.1).
        return self in {
            ProcessingMode.TOKEN_DETERMINISTIC,
            ProcessingMode.SURROGATE,
            ProcessingMode.ENCRYPT,
            ProcessingMode.HASH,
        }


class Reversibility(StrEnum):
    NONE = "none"
    VAULT = "vault"
    KEY = "key"


class ProtectionLevel(IntEnum):
    CRITICAL = 1
    DIRECT = 2
    INDIRECT = 3


class FindingKind(StrEnum):
    CONFIRMED = "confirmed"
    SUSPICIOUS = "suspicious"


class ReviewAction(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"


class UnresolvedPolicy(StrEnum):
    """Policy for unresolved questions.

    ``keep`` preserves the previous behavior: a suspicion without decision
    stays in clear text, and the profile gate blocks export. ``mask`` means
    “mask when in doubt”: unresolved questions are replaced – the protective
    direction no longer needs a hard block.
    """

    KEEP = "keep"
    MASK = "mask"


# The table is deliberately complete for all currently producible findings
# and the upcoming types listed in spec §7.2. Unknown types are not guessed
# but cause processing to fail.
ENTITY_MINIMUM_LEVEL: Final[Mapping[str, ProtectionLevel]] = MappingProxyType(
    {
        # Level 1: payment, ID, health and access data
        "IBAN_CODE": ProtectionLevel.CRITICAL,
        "CREDIT_CARD": ProtectionLevel.CRITICAL,
        "CRYPTO": ProtectionLevel.CRITICAL,
        "BIC": ProtectionLevel.CRITICAL,
        "DE_TAX_ID": ProtectionLevel.CRITICAL,
        "DE_TAX_NUMBER": ProtectionLevel.CRITICAL,
        "DE_VAT_ID": ProtectionLevel.CRITICAL,
        "AT_UID": ProtectionLevel.CRITICAL,
        "DE_SOCIAL_SECURITY": ProtectionLevel.CRITICAL,
        "AT_SVNR": ProtectionLevel.CRITICAL,
        "DE_PASSPORT": ProtectionLevel.CRITICAL,
        "DE_ID_CARD": ProtectionLevel.CRITICAL,
        "DE_HEALTH_INSURANCE": ProtectionLevel.CRITICAL,
        "SECRET": ProtectionLevel.CRITICAL,
        "API_KEY": ProtectionLevel.CRITICAL,
        "ACCESS_TOKEN": ProtectionLevel.CRITICAL,
        "PASSWORD": ProtectionLevel.CRITICAL,
        # Level 2: direct identifiers
        "PERSON": ProtectionLevel.DIRECT,
        "EMAIL_ADDRESS": ProtectionLevel.DIRECT,
        "PHONE_NUMBER": ProtectionLevel.DIRECT,
        "ADDRESS": ProtectionLevel.DIRECT,
        "STREET_ADDRESS": ProtectionLevel.DIRECT,
        "CUSTOMER_NUMBER": ProtectionLevel.DIRECT,
        "CASE_NUMBER": ProtectionLevel.DIRECT,
        "DE_KFZ": ProtectionLevel.DIRECT,
        "IP_ADDRESS": ProtectionLevel.DIRECT,
        "URL": ProtectionLevel.INDIRECT,
        # Level 3: indirect attributes
        "ORGANIZATION": ProtectionLevel.INDIRECT,
        "LOCATION": ProtectionLevel.INDIRECT,
        "DATE_TIME": ProtectionLevel.INDIRECT,
        "DE_PLZ": ProtectionLevel.INDIRECT,
        "DE_HANDELSREGISTER": ProtectionLevel.INDIRECT,
        "AT_FIRMENBUCH": ProtectionLevel.INDIRECT,
        "NRP": ProtectionLevel.INDIRECT,
        "JOB_TITLE": ProtectionLevel.INDIRECT,
        "PROFESSION": ProtectionLevel.INDIRECT,
        # Only added manually ("Art der Angabe: Sonstiges" (type of entry: other)); no detection
        # provides this type.
        "OTHER": ProtectionLevel.INDIRECT,
    }
)

OFFSET_ENCODING: Final = "utf-16"


def _known_entity_type(raw_entity_type: str) -> str:
    try:
        entity_type = normalize_entity_type(raw_entity_type)
    except (TypeError, ValueError) as error:
        raise UnknownEntityTypeError("Ungueltiger Entitaetstyp.") from error
    if entity_type not in ENTITY_MINIMUM_LEVEL:
        raise UnknownEntityTypeError(
            f"Entitaetstyp {entity_type!r} hat keine Schutzstufen-Einstufung."
        )
    return entity_type


def minimum_protection_level(entity_type: str) -> ProtectionLevel:
    """Returns the defined level or fails closed."""

    return ENTITY_MINIMUM_LEVEL[_known_entity_type(entity_type)]


@dataclass(frozen=True, slots=True)
class ProtectionOverrides:
    keep_plain: frozenset[str] = field(default_factory=frozenset)
    force_handle: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        keep_plain = _normalize_entity_collection(self.keep_plain, field_name="keep_plain")
        force_handle = _normalize_entity_collection(
            self.force_handle,
            field_name="force_handle",
        )
        conflict = keep_plain & force_handle
        if conflict:
            joined = ", ".join(sorted(conflict))
            raise ProcessingValidationError(
                f"keep_plain und force_handle muessen disjunkt sein: {joined}."
            )
        object.__setattr__(self, "keep_plain", keep_plain)
        object.__setattr__(self, "force_handle", force_handle)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> ProtectionOverrides:
        if payload is None:
            return cls()
        if not isinstance(payload, Mapping):
            raise ProcessingValidationError("overrides muss ein Objekt sein.")
        return cls(
            keep_plain=_string_collection(payload.get("keep_plain", ()), "keep_plain"),
            force_handle=_string_collection(
                payload.get("force_handle", ()),
                "force_handle",
            ),
        )


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    finding_id: str
    action: ReviewAction
    mode: ProcessingMode | None = None

    def __post_init__(self) -> None:
        finding_id = self.finding_id.strip() if isinstance(self.finding_id, str) else ""
        if not finding_id:
            raise ProcessingValidationError("Review-Entscheidung braucht eine finding_id.")
        try:
            action = ReviewAction(self.action)
            mode = ProcessingMode(self.mode) if self.mode is not None else None
        except ValueError as error:
            raise ProcessingValidationError("Ungueltige Review-Entscheidung.") from error
        object.__setattr__(self, "finding_id", finding_id)
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "mode", mode)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ReviewDecision:
        if not isinstance(payload, Mapping):
            raise ProcessingValidationError("Jede Review-Entscheidung muss ein Objekt sein.")
        raw_action = payload.get("action", payload.get("decision"))
        try:
            return cls(
                finding_id=str(payload.get("finding_id", "")),
                action=ReviewAction(raw_action),
                mode=(ProcessingMode(payload["mode"]) if payload.get("mode") is not None else None),
            )
        except (TypeError, ValueError) as error:
            raise ProcessingValidationError("Ungueltige Review-Entscheidung.") from error


@dataclass(frozen=True, slots=True)
class ManualFinding:
    """A manually entered entry in the review (spec §9.3).

    Offsets are Python offsets in the original source; conversion from the UI's UTF-16
    offsets happens at the API boundary.
    """

    entity_type: str
    start: int
    end: int
    mode: ProcessingMode | None = None
    corrects_finding_id: str | None = None

    def __post_init__(self) -> None:
        entity_type = _known_entity_type(self.entity_type)
        for value in (self.start, self.end):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ProcessingValidationError("Manuelle Offsets muessen ganze Zahlen sein.")
        if self.start < 0 or self.end <= self.start:
            raise ProcessingValidationError("Eine manuelle Stelle braucht einen nicht-leeren Span.")
        try:
            mode = ProcessingMode(self.mode) if self.mode is not None else None
        except ValueError as error:
            raise ProcessingValidationError(
                "Ungueltiger Modus an einer manuellen Stelle."
            ) from error
        corrects_finding_id = self.corrects_finding_id
        if corrects_finding_id is not None:
            if not isinstance(corrects_finding_id, str) or not corrects_finding_id.strip():
                raise ProcessingValidationError(
                    "corrects_finding_id muss eine nicht-leere Finding-ID sein."
                )
            corrects_finding_id = corrects_finding_id.strip()
        object.__setattr__(self, "entity_type", entity_type)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "corrects_finding_id", corrects_finding_id)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ManualFinding:
        if not isinstance(payload, Mapping):
            raise ProcessingValidationError("Jede manuelle Stelle muss ein Objekt sein.")
        unknown = set(payload) - {
            "entity_type",
            "start",
            "end",
            "mode",
            "corrects_finding_id",
        }
        if unknown:
            raise ProcessingValidationError(
                "Unbekannte Felder an einer manuellen Stelle: " + ", ".join(sorted(unknown))
            )
        try:
            return cls(
                entity_type=str(payload.get("entity_type", "")),
                start=payload.get("start"),  # type: ignore[arg-type]
                end=payload.get("end"),  # type: ignore[arg-type]
                mode=payload.get("mode"),  # type: ignore[arg-type]
                corrects_finding_id=payload.get("corrects_finding_id"),  # type: ignore[arg-type]
            )
        except (TypeError, ValueError) as error:
            if isinstance(error, ProcessingValidationError):
                raise
            raise ProcessingValidationError("Ungueltige manuelle Stelle.") from error

    def as_finding(self, text: str) -> Finding:
        if self.end > len(text):
            raise ProcessingValidationError("Manuelle Stelle liegt ausserhalb des Quelltexts.")
        return Finding(
            entity_type=self.entity_type,
            start=self.start,
            end=self.end,
            # Manual entries are not estimates; they are treated like a master-data hit.
            score=1.0,
            source=FindingSource.MANUAL,
            detector="review",
        )


@dataclass(frozen=True, slots=True)
class ProcessingRequest:
    mode: ProcessingMode
    protection_level: ProtectionLevel = ProtectionLevel.DIRECT
    overrides: ProtectionOverrides = field(default_factory=ProtectionOverrides)
    policies: Mapping[str, ProcessingMode] = field(default_factory=lambda: MappingProxyType({}))
    vault_reversible: bool = True
    decisions: tuple[ReviewDecision, ...] = ()
    manual_findings: tuple[ManualFinding, ...] = ()
    unresolved: UnresolvedPolicy = UnresolvedPolicy.KEEP
    #: Confirmed but uncertain hits that count as questions. Suspicious spots are always
    #: questions and must not appear here.
    question_ids: frozenset[str] = frozenset()
    #: Language of placeholders (``[Name]`` / ``[Name]``, ``[Ort]`` / ``[Place]``).
    placeholder_language: str = "de"
    #: Since phase J: language per source segment ``(start, end, language)``.
    #: A placeholder or invented name follows the language at its position; otherwise
    #: ``placeholder_language`` applies.
    placeholder_languages: tuple[tuple[int, int, str], ...] = ()

    def language_at(self, offset: int) -> str:
        for start, end, language in self.placeholder_languages:
            if start <= offset < end:
                return language
        return self.placeholder_language

    def __post_init__(self) -> None:
        try:
            mode = ProcessingMode(self.mode)
            if isinstance(self.protection_level, bool):
                raise ValueError
            level = ProtectionLevel(self.protection_level)
        except (TypeError, ValueError) as error:
            raise ProcessingValidationError("Ungueltiger Modus oder Schutzstufe.") from error
        overrides = (
            self.overrides
            if isinstance(self.overrides, ProtectionOverrides)
            else ProtectionOverrides.from_dict(self.overrides)
        )
        policies = _normalize_policies(self.policies)
        if not isinstance(self.vault_reversible, bool):
            raise ProcessingValidationError("vault_reversible muss boolesch sein.")
        decisions = tuple(self.decisions)
        if not all(isinstance(item, ReviewDecision) for item in decisions):
            raise ProcessingValidationError(
                "decisions darf nur ReviewDecision-Eintraege enthalten."
            )
        ids = [decision.finding_id for decision in decisions]
        if len(ids) != len(set(ids)):
            raise ProcessingValidationError("Eine finding_id darf nur einmal entschieden werden.")
        manual = tuple(self.manual_findings)
        if not all(isinstance(item, ManualFinding) for item in manual):
            raise ProcessingValidationError(
                "manual_findings darf nur ManualFinding-Eintraege enthalten."
            )
        spans = sorted((item.start, item.end) for item in manual)
        for (_, previous_end), (current_start, _) in zip(spans, spans[1:], strict=False):
            if current_start < previous_end:
                raise ProcessingValidationError("Manuelle Stellen duerfen sich nicht ueberlappen.")
        correction_ids = [
            item.corrects_finding_id for item in manual if item.corrects_finding_id is not None
        ]
        if len(correction_ids) != len(set(correction_ids)):
            raise ProcessingValidationError(
                "Ein automatischer Treffer darf nur einmal in seinen Grenzen korrigiert werden."
            )

        try:
            unresolved = UnresolvedPolicy(self.unresolved)
        except ValueError as error:
            raise ProcessingValidationError("unresolved muss 'keep' oder 'mask' sein.") from error
        if self.placeholder_language not in LANGUAGES:
            raise ProcessingValidationError("Unbekannte Platzhaltersprache.")
        placeholder_languages = tuple(
            (int(start), int(end), str(language))
            for start, end, language in self.placeholder_languages
        )
        if any(
            language not in LANGUAGES or start < 0 or end < start
            for start, end, language in placeholder_languages
        ):
            raise ProcessingValidationError("Ungueltige Sprachbereiche der Platzhalter.")
        object.__setattr__(self, "placeholder_languages", placeholder_languages)
        question_ids = frozenset(self.question_ids)
        if not all(isinstance(item, str) for item in question_ids):
            raise ProcessingValidationError("question_ids darf nur Finding-IDs enthalten.")

        object.__setattr__(self, "unresolved", unresolved)
        object.__setattr__(self, "question_ids", question_ids)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "protection_level", level)
        object.__setattr__(self, "overrides", overrides)
        object.__setattr__(self, "policies", policies)
        object.__setattr__(self, "decisions", decisions)
        object.__setattr__(self, "manual_findings", manual)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ProcessingRequest:
        """Strict API-adjacent parser that does not depend on FastAPI."""

        if not isinstance(payload, Mapping):
            raise ProcessingValidationError("Apply-Anfrage muss ein Objekt sein.")
        try:
            mode = ProcessingMode(payload["mode"])
            raw_level = payload.get("protection_level", 2)
            if isinstance(raw_level, bool):
                raise ValueError
            level = ProtectionLevel(raw_level)
            policies = payload.get("policies", {})
            if not isinstance(policies, Mapping):
                raise ProcessingValidationError("policies muss ein Objekt sein.")
            raw_decisions = payload.get("decisions", ())
            if isinstance(raw_decisions, (str, bytes)) or not isinstance(raw_decisions, Sequence):
                raise ProcessingValidationError("decisions muss eine Liste sein.")
            reversible = payload.get("vault_reversible", payload.get("reversible", True))
            if not isinstance(reversible, bool):
                raise ProcessingValidationError("vault_reversible muss boolesch sein.")
            raw_manual = payload.get("manual_findings", ())
            if isinstance(raw_manual, (str, bytes)) or not isinstance(raw_manual, Sequence):
                raise ProcessingValidationError("manual_findings muss eine Liste sein.")
            return cls(
                mode=mode,
                protection_level=level,
                overrides=ProtectionOverrides.from_dict(
                    payload.get("overrides")  # type: ignore[arg-type]
                ),
                policies=policies,  # type: ignore[arg-type]
                vault_reversible=reversible,
                decisions=tuple(ReviewDecision.from_dict(item) for item in raw_decisions),
                manual_findings=tuple(ManualFinding.from_dict(item) for item in raw_manual),
            )
        except KeyError as error:
            raise ProcessingValidationError("Apply-Anfrage braucht das Feld 'mode'.") from error
        except (TypeError, ValueError) as error:
            if isinstance(error, ProcessingValidationError):
                raise
            raise ProcessingValidationError("Ungueltige Apply-Anfrage.") from error


@dataclass(frozen=True, slots=True)
class AppliedFinding:
    """Metadata without content for an actually performed replacement."""

    finding_id: str
    kind: FindingKind
    entity_type: str
    source: str
    score: float
    mode: ProcessingMode
    reversibility: Reversibility
    original_start: int
    original_end: int
    output_start: int
    output_end: int

    def as_dict(self) -> dict[str, object]:
        return {
            "finding_id": self.finding_id,
            "kind": self.kind,
            "entity_type": self.entity_type,
            "source": self.source,
            "score": round(self.score, 6),
            "mode": self.mode,
            "reversibility": self.reversibility,
            "original": {"start": self.original_start, "end": self.original_end},
            "output": {"start": self.output_start, "end": self.output_end},
            "replacement_length": self.output_end - self.output_start,
        }


@dataclass(frozen=True, slots=True)
class ProcessingFacts:
    protection_level: ProtectionLevel
    default_mode: ProcessingMode
    effective_modes: tuple[ProcessingMode, ...]
    mode_counts: Mapping[ProcessingMode, int]
    mode_counts_by_entity: Mapping[str, Mapping[ProcessingMode, int]]
    reversibility_by_mode: Mapping[ProcessingMode, Reversibility]
    has_reversible_changes: bool
    has_irreversible_changes: bool
    has_vault_reversible_changes: bool
    has_key_reversible_changes: bool
    has_nonreturning_pseudonyms: bool
    only_anonymization_eligible_modes: bool
    contains_surrogates: bool
    irreversible_count: int
    irreversible_counts_by_entity: Mapping[str, int]
    has_plaintext_overrides: bool
    plaintext_override_entities: tuple[str, ...]
    force_handle_entities: tuple[str, ...]
    has_unhandled_confirmed_findings: bool
    has_unresolved_suspicions: bool
    residual_risk_status: ResidualRiskStatus

    @property
    def level_three(self) -> bool:
        return self.protection_level is ProtectionLevel.INDIRECT

    @property
    def residual_clear(self) -> bool:
        return self.residual_risk_status is ResidualRiskStatus.CLEAR

    def as_dict(self) -> dict[str, object]:
        return {
            "protection_level": int(self.protection_level),
            "level_three": self.level_three,
            "default_mode": self.default_mode,
            "effective_modes": list(self.effective_modes),
            "mode_counts": {
                "by_mode": {mode.value: self.mode_counts[mode] for mode in self.effective_modes},
                "by_entity": {
                    entity: {mode.value: count for mode, count in counts.items()}
                    for entity, counts in self.mode_counts_by_entity.items()
                },
            },
            "reversibility_by_mode": {
                mode.value: reversibility
                for mode, reversibility in self.reversibility_by_mode.items()
            },
            "has_reversible_changes": self.has_reversible_changes,
            "has_irreversible_changes": self.has_irreversible_changes,
            "has_vault_reversible_changes": self.has_vault_reversible_changes,
            "has_key_reversible_changes": self.has_key_reversible_changes,
            "has_nonreturning_pseudonyms": self.has_nonreturning_pseudonyms,
            "only_anonymization_eligible_modes": (self.only_anonymization_eligible_modes),
            "contains_surrogates": self.contains_surrogates,
            "irreversible_counts": {
                "total": self.irreversible_count,
                "by_entity": dict(self.irreversible_counts_by_entity),
            },
            "has_plaintext_overrides": self.has_plaintext_overrides,
            "plaintext_override_entities": list(self.plaintext_override_entities),
            "force_handle_entities": list(self.force_handle_entities),
            "has_unhandled_confirmed_findings": (self.has_unhandled_confirmed_findings),
            "has_unresolved_suspicions": self.has_unresolved_suspicions,
            "residual_risk_status": self.residual_risk_status,
            "residual_clear": self.residual_clear,
        }


@dataclass(frozen=True, slots=True)
class ProcessingReport:
    """Report without original values, replacements or text snippets."""

    facts: ProcessingFacts
    applied_counts_by_entity: Mapping[str, int]
    kept_plain_counts_by_entity: Mapping[str, int]
    rejected_counts_by_entity: Mapping[str, int]
    accepted_suspicion_count: int
    unresolved_suspicion_count: int
    # Manual entries are stored separately, even though they are counted in
    # ``applied_counts_by_entity``:
    # the report must show what detection did and what a human had to add.
    manual_counts_by_entity: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    # Only an explicit empty target ID creates a manual entry for boundary correction;
    # geometric overlap alone remains ambiguous. Both categories stay separate so the visible
    # report lists the same interventions as the audit log (spec §9.3 and acceptance 7.2).
    manual_added_counts_by_entity: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({})
    )
    adjusted_span_counts_by_entity: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({})
    )
    #: Open questions that were replaced after “If in doubt, mask” without a decision.
    #: The report and audit count them separately.
    unresolved_masked_counts_by_entity: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({})
    )

    @property
    def unresolved_masked_count(self) -> int:
        return sum(self.unresolved_masked_counts_by_entity.values())

    @property
    def manual_count(self) -> int:
        return sum(self.manual_counts_by_entity.values())

    @property
    def manual_added_count(self) -> int:
        return sum(self.manual_added_counts_by_entity.values())

    @property
    def adjusted_span_count(self) -> int:
        return sum(self.adjusted_span_counts_by_entity.values())

    def as_dict(self) -> dict[str, object]:
        notices: list[str] = []
        if self.facts.contains_surrogates:
            notices.append("enthält Decknamen")
        if self.facts.has_nonreturning_pseudonyms:
            notices.append(
                "Einweg-Prüfsummen und nicht rückholbare Kürzel bleiben "
                "weiterhin Pseudonymisierung."
            )
        if self.manual_added_count:
            notices.append(
                f"{self.manual_added_count} Stelle(n) wurden im Review von Hand nachgetragen."
            )
        if self.adjusted_span_count:
            notices.append(
                f"Bei {self.adjusted_span_count} Stelle(n) wurden die Span-Grenzen korrigiert."
            )
        if self.unresolved_masked_count:
            notices.append(
                f"{self.unresolved_masked_count} offene Stelle(n) wurden "
                "ohne Entscheidung maskiert."
            )
        return {
            "facts": self.facts.as_dict(),
            "applied_counts_by_entity": dict(self.applied_counts_by_entity),
            "kept_plain_counts_by_entity": dict(self.kept_plain_counts_by_entity),
            "rejected_counts_by_entity": dict(self.rejected_counts_by_entity),
            "manual_counts_by_entity": dict(self.manual_counts_by_entity),
            "manual_count": self.manual_count,
            "manual_added_counts_by_entity": dict(self.manual_added_counts_by_entity),
            "manual_added_count": self.manual_added_count,
            "adjusted_span_counts_by_entity": dict(self.adjusted_span_counts_by_entity),
            "adjusted_span_count": self.adjusted_span_count,
            "accepted_suspicion_count": self.accepted_suspicion_count,
            "unresolved_suspicion_count": self.unresolved_suspicion_count,
            "unresolved_masked_counts_by_entity": dict(self.unresolved_masked_counts_by_entity),
            "unresolved_masked_count": self.unresolved_masked_count,
            "notices": notices,
        }


@dataclass(frozen=True, slots=True)
class ProcessingResult:
    text: str
    applied_findings: tuple[AppliedFinding, ...]
    residual_risk: ResidualRiskResult
    report: ProcessingReport
    offset_encoding: str = OFFSET_ENCODING
    #: Calculated but not persisted pending mappings from a dry-run.
    #: They are written only when the result is saved or copied.
    #: Not part of the API response – ``as_dict`` deliberately omits them.
    pending_mappings: tuple[PendingMapping, ...] = field(default=(), repr=False)

    @property
    def facts(self) -> ProcessingFacts:
        return self.report.facts

    def as_dict(self) -> dict[str, object]:
        facts = self.facts.as_dict()
        return {
            "text": self.text,
            "offset_encoding": self.offset_encoding,
            "applied_findings": [item.as_dict() for item in self.applied_findings],
            "residual_risk": _residual_risk_api_dict(self.residual_risk),
            # The API forwards the contract directly; the same facts are kept
            # bundled for new clients.
            **facts,
            "result_facts": facts,
            "report": self.report.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class _SelectedFinding:
    finding: Finding
    finding_id: str
    kind: FindingKind
    entity_type: str
    mode: ProcessingMode
    reversibility: Reversibility


class _Staging:
    """Collects mappings from a dry-run, ensuring each token appears only once."""

    def __init__(self) -> None:
        self._by_token: dict[str, PendingMapping] = {}
        self.reserved_surrogates: dict[str, str] = {}

    def add(self, pending: PendingMapping) -> str:
        known = self._by_token.get(pending.token)
        if known is None:
            self._by_token[pending.token] = pending
            return pending.token
        # When tokens clash, keep the spelling the vault would retain;
        # otherwise the result would depend on insertion order.
        # An already assigned pseudonym is never lost.
        winner = (
            pending
            if canonical_rank(pending.canonical) > canonical_rank(known.canonical)
            else known
        )
        self._by_token[pending.token] = PendingMapping(
            token=winner.token,
            entity_type=winner.entity_type,
            normalized_value=winner.normalized_value,
            canonical=winner.canonical,
            surrogate=known.surrogate or pending.surrogate,
        )
        return pending.token

    @property
    def pending(self) -> tuple[PendingMapping, ...]:
        return tuple(self._by_token.values())


@dataclass(frozen=True, slots=True)
class _PlannedReplacement:
    selected: _SelectedFinding
    value: str = field(repr=False)


class TextProcessor:
    """Apply protection level, review decisions, and operators to a raw-text job."""

    def __init__(
        self,
        vault: TokenVault,
        *,
        residual_risk: ResidualRiskAnalyzer | None = None,
    ) -> None:
        self.vault = vault
        self.residual_risk = residual_risk or ResidualRiskAnalyzer()
        self._encrypt = Encrypt()
        self._decrypt = Decrypt()
        self._hash = Hash()

    def apply(
        self,
        text: str,
        detection: DetectionResult,
        request: ProcessingRequest,
        *,
        commit: bool = True,
    ) -> ProcessingResult:
        """Compute the cleaned text.

        ``commit=False`` runs a dry-run (API v2): short codes and deck names are
        the same as for writing, but the mappings end up only in
        ``ProcessingResult.pending_mappings``.
        """

        if not isinstance(text, str):
            raise TypeError("Quelltext muss Text sein.")
        if not isinstance(detection, DetectionResult):
            raise TypeError("detection muss ein DetectionResult sein.")
        if not isinstance(request, ProcessingRequest):
            raise TypeError("request muss ein ProcessingRequest sein.")

        catalog = _finding_catalog(text, detection, request.manual_findings)
        decisions = {decision.finding_id: decision for decision in request.decisions}
        unknown_decisions = sorted(decisions.keys() - catalog.keys())
        if unknown_decisions:
            raise ProcessingValidationError(
                "Mindestens eine Review-Entscheidung verweist auf kein Finding."
            )

        selected: list[_SelectedFinding] = []
        remaining_entity_types: list[str] = []
        kept_plain = Counter[str]()
        rejected = Counter[str]()
        manual_applied = Counter[str]()
        manual_added = Counter[str]()
        adjusted_spans = Counter[str]()
        unresolved_masked = Counter[str]()
        accepted_suspicion_count = 0
        unresolved_suspicion_count = 0
        unresolved_suspicion_entities = Counter[str]()
        _validate_manual_corrections(catalog, decisions)

        for finding_id_value, entry in catalog.items():
            finding, kind, entity_type = entry.finding, entry.kind, entry.entity_type
            decision = decisions.get(finding_id_value)
            accepted = kind is FindingKind.CONFIRMED
            open_question = decision is None and (
                kind is FindingKind.SUSPICIOUS or finding_id_value in request.question_ids
            )
            if decision is not None:
                accepted = decision.action is ReviewAction.ACCEPT
            elif kind is FindingKind.SUSPICIOUS:
                accepted = request.unresolved is UnresolvedPolicy.MASK

            if not accepted:
                if decision is not None and decision.action is ReviewAction.REJECT:
                    rejected[entity_type] += 1
                elif kind is FindingKind.SUSPICIOUS:
                    unresolved_suspicion_count += 1
                    unresolved_suspicion_entities[entity_type] += 1
                continue

            if kind is FindingKind.SUSPICIOUS and not open_question:
                accepted_suspicion_count += 1

            # Manual markings are always replaced, even if the protection level
            # would otherwise leave the type in clear text. An explicit review
            # instruction overrides the default – otherwise the UI would silently
            # drop the marking.
            if not entry.manual and not _should_handle(
                entity_type, request.protection_level, request.overrides
            ):
                kept_plain[entity_type] += 1
                remaining_entity_types.append(entity_type)
                continue
            if open_question:
                unresolved_masked[entity_type] += 1
            if entry.manual:
                manual_applied[entity_type] += 1
                if entry.manual_corrects_finding_id is not None:
                    adjusted_spans[entity_type] += 1
                else:
                    manual_added[entity_type] += 1

            mode = (
                decision.mode
                if decision is not None and decision.mode is not None
                else entry.manual_mode
                if entry.manual_mode is not None
                else request.policies.get(entity_type, request.mode)
            )
            reversibility = _reversibility_for(mode, request.vault_reversible)
            selected.append(
                _SelectedFinding(
                    finding=finding,
                    finding_id=finding_id_value,
                    kind=kind,
                    entity_type=entity_type,
                    mode=mode,
                    reversibility=reversibility,
                )
            )

        repetitions = _manual_repetitions(text, selected, catalog, decisions)
        for item in repetitions:
            manual_applied[item.entity_type] += 1
        selected.extend(repetitions)

        _validate_selected(selected, request)
        staging = None if commit else _Staging()
        plans = tuple(
            _PlannedReplacement(item, self._replacement_for(text, item, request, staging))
            for item in selected
        )
        cleaned_text = replace_back_to_front(
            text,
            (
                SpanReplacement(
                    plan.selected.finding.start,
                    plan.selected.finding.end,
                    plan.value,
                )
                for plan in plans
            ),
        )
        applied = _applied_metadata(text, cleaned_text, plans)
        residual = self._run_residual_risk(
            cleaned_text,
            remaining_entity_types=remaining_entity_types,
            replacement_spans=_replacement_output_spans(plans),
        )
        facts = _build_facts(
            request,
            selected,
            residual,
            kept_plain_entities=kept_plain,
            unresolved_suspicion_entities=unresolved_suspicion_entities,
        )
        report = ProcessingReport(
            facts=facts,
            applied_counts_by_entity=MappingProxyType(
                dict(sorted(Counter(item.entity_type for item in selected).items()))
            ),
            kept_plain_counts_by_entity=MappingProxyType(dict(sorted(kept_plain.items()))),
            rejected_counts_by_entity=MappingProxyType(dict(sorted(rejected.items()))),
            manual_counts_by_entity=MappingProxyType(dict(sorted(manual_applied.items()))),
            manual_added_counts_by_entity=MappingProxyType(dict(sorted(manual_added.items()))),
            adjusted_span_counts_by_entity=MappingProxyType(dict(sorted(adjusted_spans.items()))),
            accepted_suspicion_count=accepted_suspicion_count,
            unresolved_suspicion_count=unresolved_suspicion_count,
            unresolved_masked_counts_by_entity=MappingProxyType(
                dict(sorted(unresolved_masked.items()))
            ),
        )
        return ProcessingResult(
            text=cleaned_text,
            applied_findings=applied,
            residual_risk=residual,
            report=report,
            pending_mappings=staging.pending if staging is not None else (),
        )

    def decrypt_encrypted_value(self, value: str) -> str:
        """Decrypts a single encryption output produced by this module."""

        if not isinstance(value, str) or not value:
            raise ProcessingValidationError("Ungueltiges verschluesseltes Feld.")
        try:
            result = self._decrypt.operate(
                value,
                {"key": self.vault.encryption_key},
            )
        except Exception as error:
            raise ProcessingValidationError(
                "Verschluesseltes Feld konnte nicht aufgeloest werden."
            ) from error
        if not isinstance(result, str):
            raise ProcessingError("Presidio Decrypt lieferte keinen Text.")
        return result

    def _replacement_for(
        self,
        text: str,
        selected: _SelectedFinding,
        request: ProcessingRequest,
        staging: _Staging | None = None,
    ) -> str:
        value = text[selected.finding.start : selected.finding.end]
        mode = selected.mode
        if mode is ProcessingMode.TOKEN_DETERMINISTIC:
            if staging is not None and request.vault_reversible:
                return staging.add(self.vault.prepare_token(selected.entity_type, value))
            return self.vault.token_for(
                selected.entity_type,
                value,
                reversible=request.vault_reversible and staging is None,
            )
        if mode is ProcessingMode.SURROGATE:
            if staging is not None:
                pending = self.vault.prepare_surrogate(
                    selected.entity_type,
                    value,
                    reserved=staging.reserved_surrogates,
                    language=request.language_at(selected.finding.start),
                )
                staging.add(pending)
                assert pending.surrogate is not None
                return pending.surrogate
            _, surrogate = self.vault.surrogate_for(selected.entity_type, value)
            return surrogate
        if mode is ProcessingMode.ENCRYPT:
            encrypted = self._encrypt.operate(
                value,
                {"key": self.vault.encryption_key},
            )
            if not isinstance(encrypted, str):
                raise ProcessingError("Presidio Encrypt lieferte keinen Text.")
            return encrypted
        if mode is ProcessingMode.HASH:
            hashed = self._hash.operate(
                normalize_value(value),
                {
                    "hash_type": "sha256",
                    "salt": self.vault.hash_salt,
                },
            )
            if not isinstance(hashed, str):
                raise ProcessingError("Presidio Hash lieferte keinen Text.")
            return hashed
        if mode is ProcessingMode.PLACEHOLDER:
            return placeholder_for(
                selected.entity_type, request.language_at(selected.finding.start)
            )
        if mode is ProcessingMode.MASK:
            return "•" * len(value)
        if mode is ProcessingMode.REMOVE:
            return ""
        raise AssertionError(f"Nicht behandelter Modus: {mode}")

    def _run_residual_risk(
        self,
        cleaned_text: str,
        *,
        remaining_entity_types: Iterable[str],
        replacement_spans: Iterable[tuple[int, int]],
    ) -> ResidualRiskResult:
        try:
            return self.residual_risk.analyze(
                cleaned_text,
                remaining_entity_types=remaining_entity_types,
                replacement_spans=replacement_spans,
            )
        except Exception:
            # A heuristic error must not expose the cleaned text in clear text.
            # For anonymity labeling, ``failed`` is a hard, fail-closed negative fact.
            counts = MappingProxyType({risk_class: 0 for risk_class in ResidualRiskClass})
            return ResidualRiskResult(
                status=ResidualRiskStatus.FAILED,
                classes=(),
                counts=counts,
                threshold=self.residual_risk.warn_at_classes,
            )


def finding_id(finding: Finding, *, kind: FindingKind | str) -> str:
    """Stable, content-free ID for review decisions."""

    try:
        normalized_kind = FindingKind(kind)
        entity_type = normalize_entity_type(finding.entity_type)
    except (TypeError, ValueError) as error:
        raise ProcessingValidationError("Finding kann nicht identifiziert werden.") from error
    source = str(finding.source)
    detector = finding.detector or ""
    material = "\x00".join(
        (
            normalized_kind.value,
            entity_type,
            str(finding.start),
            str(finding.end),
            source,
            detector,
        )
    ).encode("utf-8")
    return f"f_{hashlib.sha256(material).hexdigest()[:20]}"


@dataclass(frozen=True, slots=True)
class _CatalogEntry:
    finding: Finding
    kind: FindingKind
    entity_type: str
    manual_mode: ProcessingMode | None = None
    manual_corrects_finding_id: str | None = None

    @property
    def manual(self) -> bool:
        return self.finding.source is FindingSource.MANUAL


def _finding_catalog(
    text: str,
    detection: DetectionResult,
    manual_findings: Sequence[ManualFinding] = (),
) -> dict[str, _CatalogEntry]:
    catalog: dict[str, _CatalogEntry] = {}
    for kind, findings in (
        (FindingKind.CONFIRMED, detection.confirmed),
        (FindingKind.SUSPICIOUS, detection.suspicious),
    ):
        for finding in findings:
            if finding.end > len(text):
                raise ProcessingValidationError("Finding liegt ausserhalb des Quelltexts.")
            entity_type = _known_entity_type(finding.entity_type)
            identifier = finding_id(finding, kind=kind)
            if identifier in catalog:
                raise ProcessingValidationError("DetectionResult enthaelt doppelte Findings.")
            catalog[identifier] = _CatalogEntry(finding, kind, entity_type)

    # Manual entries are added last and always marked as confirmed:
    # marking a spot in the review implicitly decides it.
    for manual in manual_findings:
        finding = manual.as_finding(text)
        identifier = finding_id(finding, kind=FindingKind.CONFIRMED)
        if identifier in catalog:
            raise ProcessingValidationError("Eine manuelle Stelle wurde doppelt eingetragen.")
        catalog[identifier] = _CatalogEntry(
            finding,
            FindingKind.CONFIRMED,
            manual.entity_type,
            manual_mode=manual.mode,
            manual_corrects_finding_id=manual.corrects_finding_id,
        )
    return catalog


def _validate_manual_corrections(
    catalog: Mapping[str, _CatalogEntry],
    decisions: Mapping[str, ReviewDecision],
) -> None:
    """Check the explicit, content-free origin of each boundary correction."""

    for entry in catalog.values():
        target_id = entry.manual_corrects_finding_id
        if not entry.manual or target_id is None:
            continue
        target = catalog.get(target_id)
        if target is None or target.manual:
            raise ProcessingValidationError(
                "Grenzkorrektur verweist auf keinen automatischen Treffer."
            )
        if target.entity_type != entry.entity_type:
            raise ProcessingValidationError(
                "Grenzkorrektur muss denselben Entitaetstyp wie ihr Ziel behalten."
            )
        decision = decisions.get(target_id)
        if decision is None or decision.action is not ReviewAction.REJECT:
            raise ProcessingValidationError(
                "Grenzkorrektur muss ihren bisherigen Treffer ausdruecklich verwerfen."
            )


def _should_handle(
    entity_type: str,
    level: ProtectionLevel,
    overrides: ProtectionOverrides,
) -> bool:
    if entity_type in overrides.keep_plain:
        return False
    if entity_type in overrides.force_handle:
        return True
    return minimum_protection_level(entity_type) <= level


def _reversibility_for(
    mode: ProcessingMode,
    vault_reversible: bool,
) -> Reversibility:
    if mode is ProcessingMode.TOKEN_DETERMINISTIC:
        return Reversibility.VAULT if vault_reversible else Reversibility.NONE
    if mode is ProcessingMode.SURROGATE:
        return Reversibility.VAULT
    if mode is ProcessingMode.ENCRYPT:
        return Reversibility.KEY
    return Reversibility.NONE


#: Detector for occurrences that trigger a manual entry.
MANUAL_REPETITION_DETECTOR: Final = "review_repetition"


def _manual_repetitions(
    text: str,
    selected: Sequence[_SelectedFinding],
    catalog: Mapping[str, _CatalogEntry],
    decisions: Mapping[str, ReviewDecision],
) -> list[_SelectedFinding]:
    """Additional occurrences of a value that was manually marked.

    If “1132784527” is marked once, it refers to the policy number – not the
    single occurrence among many. The PDF post-text check already requires that
    a masked value no longer appears anywhere; without this step saving would
    fail at the end and the preview would differ from the final output. The
    search follows the same logic as in the review
    (``iter_value_occurrences``).

    Locations that will be replaced anyway, or that the user explicitly kept,
    are skipped. The review deliberately leaves kept values untouched.
    """

    sources: dict[str, _SelectedFinding] = {}
    for item in selected:
        if catalog[item.finding_id].manual:
            value = text[item.finding.start : item.finding.end]
            sources.setdefault(compact_value(value), item)
    if not sources:
        return []
    blocked = [(item.finding.start, item.finding.end) for item in selected]
    blocked.extend(
        (catalog[identifier].finding.start, catalog[identifier].finding.end)
        for identifier, decision in decisions.items()
        if decision.action is ReviewAction.REJECT and identifier in catalog
    )
    added: list[_SelectedFinding] = []
    try:
        occurrences = tuple(
            iter_value_occurrences(
                text,
                (text[item.finding.start : item.finding.end] for item in sources.values()),
            )
        )
    except DuplicateReviewCapacityError:
        # More values than the search can safely handle: keep only the
        # manually marked locations and stop the post-text check.
        return []
    for start, end, key in sorted(occurrences):
        if any(start < right and left < end for left, right in blocked):
            continue
        source = sources[key]
        finding = Finding(
            entity_type=source.finding.entity_type,
            start=start,
            end=end,
            score=1.0,
            source=FindingSource.MANUAL,
            text=text[start:end],
            detector=MANUAL_REPETITION_DETECTOR,
        )
        added.append(
            _SelectedFinding(
                finding=finding,
                finding_id=finding_id(finding, kind=FindingKind.CONFIRMED),
                kind=FindingKind.CONFIRMED,
                entity_type=source.entity_type,
                mode=source.mode,
                reversibility=source.reversibility,
            )
        )
        blocked.append((start, end))
    return added


def _validate_selected(
    selected: Sequence[_SelectedFinding],
    request: ProcessingRequest,
) -> None:
    if not request.vault_reversible and any(
        item.mode is ProcessingMode.SURROGATE for item in selected
    ):
        raise ProcessingValidationError("Decknamen brauchen einen reversiblen Token-Tresor.")
    ordered = sorted(
        selected,
        key=lambda item: (item.finding.start, item.finding.end),
    )
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if current.finding.start < previous.finding.end:
            raise ProcessingValidationError(
                "Zu behandelnde Findings duerfen sich nicht ueberlappen."
            )


def _applied_metadata(
    source_text: str,
    output_text: str,
    plans: Sequence[_PlannedReplacement],
) -> tuple[AppliedFinding, ...]:
    applied: list[AppliedFinding] = []
    delta = 0
    for plan in sorted(
        plans,
        key=lambda item: (item.selected.finding.start, item.selected.finding.end),
    ):
        selected = plan.selected
        finding = selected.finding
        output_start_python = finding.start + delta
        output_end_python = output_start_python + len(plan.value)
        applied.append(
            AppliedFinding(
                finding_id=selected.finding_id,
                kind=selected.kind,
                entity_type=selected.entity_type,
                source=str(finding.source),
                score=finding.score,
                mode=selected.mode,
                reversibility=selected.reversibility,
                original_start=_utf16_offset(source_text, finding.start),
                original_end=_utf16_offset(source_text, finding.end),
                output_start=_utf16_offset(output_text, output_start_python),
                output_end=_utf16_offset(output_text, output_end_python),
            )
        )
        delta += len(plan.value) - finding.length
    return tuple(applied)


def _replacement_output_spans(
    plans: Sequence[_PlannedReplacement],
) -> tuple[tuple[int, int], ...]:
    """Python spans of replacements in the cleaned text."""

    spans: list[tuple[int, int]] = []
    delta = 0
    for plan in sorted(
        plans,
        key=lambda item: (item.selected.finding.start, item.selected.finding.end),
    ):
        finding = plan.selected.finding
        start = finding.start + delta
        end = start + len(plan.value)
        if end > start:
            spans.append((start, end))
        delta += len(plan.value) - finding.length
    return tuple(spans)


def _build_facts(
    request: ProcessingRequest,
    selected: Sequence[_SelectedFinding],
    residual: ResidualRiskResult,
    *,
    kept_plain_entities: Mapping[str, int],
    unresolved_suspicion_entities: Mapping[str, int],
) -> ProcessingFacts:
    mode_counts_counter = Counter(item.mode for item in selected)
    effective_modes = tuple(mode for mode in ProcessingMode if mode_counts_counter.get(mode, 0) > 0)
    by_entity: dict[str, Counter[ProcessingMode]] = {}
    for item in selected:
        by_entity.setdefault(item.entity_type, Counter())[item.mode] += 1
    reversibility_by_mode = {
        mode: next(item.reversibility for item in selected if item.mode is mode)
        for mode in effective_modes
    }
    reversibilities = tuple(item.reversibility for item in selected)
    irreversible = tuple(item for item in selected if item.reversibility is Reversibility.NONE)
    plaintext_entities = (
        set(request.overrides.keep_plain)
        | set(kept_plain_entities)
        | set(unresolved_suspicion_entities)
    )
    return ProcessingFacts(
        protection_level=request.protection_level,
        default_mode=request.mode,
        effective_modes=effective_modes,
        mode_counts=MappingProxyType({mode: mode_counts_counter[mode] for mode in effective_modes}),
        mode_counts_by_entity=MappingProxyType(
            {
                entity: MappingProxyType(dict(sorted(counts.items(), key=lambda item: item[0])))
                for entity, counts in sorted(by_entity.items())
            }
        ),
        reversibility_by_mode=MappingProxyType(reversibility_by_mode),
        has_reversible_changes=any(
            reversibility is not Reversibility.NONE for reversibility in reversibilities
        ),
        has_irreversible_changes=any(
            reversibility is Reversibility.NONE for reversibility in reversibilities
        ),
        has_vault_reversible_changes=any(
            reversibility is Reversibility.VAULT for reversibility in reversibilities
        ),
        has_key_reversible_changes=any(
            reversibility is Reversibility.KEY for reversibility in reversibilities
        ),
        has_nonreturning_pseudonyms=any(
            item.mode is ProcessingMode.HASH
            or (
                item.mode is ProcessingMode.TOKEN_DETERMINISTIC
                and item.reversibility is Reversibility.NONE
            )
            for item in selected
        ),
        only_anonymization_eligible_modes=bool(selected)
        and all(item.mode.anonymization_eligible for item in selected),
        contains_surrogates=any(item.mode is ProcessingMode.SURROGATE for item in selected),
        irreversible_count=len(irreversible),
        irreversible_counts_by_entity=MappingProxyType(
            dict(sorted(Counter(item.entity_type for item in irreversible).items()))
        ),
        # Conservative: configured exceptions and confirmed plaintext findings,
        # plus any remaining suspicion spots, prevent a privacy guarantee.
        has_plaintext_overrides=bool(plaintext_entities),
        plaintext_override_entities=tuple(sorted(plaintext_entities)),
        force_handle_entities=tuple(sorted(request.overrides.force_handle)),
        has_unhandled_confirmed_findings=bool(kept_plain_entities),
        has_unresolved_suspicions=bool(unresolved_suspicion_entities),
        residual_risk_status=residual.status,
    )


def _normalize_entity_collection(
    values: Iterable[str],
    *,
    field_name: str,
) -> frozenset[str]:
    if isinstance(values, (str, bytes)):
        raise ProcessingValidationError(f"{field_name} muss eine Liste sein.")
    try:
        return frozenset(_known_entity_type(value) for value in values)
    except TypeError as error:
        raise ProcessingValidationError(f"{field_name} muss eine Liste sein.") from error


def _string_collection(value: object, field_name: str) -> frozenset[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ProcessingValidationError(f"{field_name} muss eine Liste sein.")
    if not all(isinstance(item, str) for item in value):
        raise ProcessingValidationError(f"{field_name} darf nur Textwerte enthalten.")
    return frozenset(value)


def _normalize_policies(
    policies: Mapping[str, ProcessingMode | str],
) -> Mapping[str, ProcessingMode]:
    if not isinstance(policies, Mapping):
        raise ProcessingValidationError("policies muss ein Objekt sein.")
    normalized: dict[str, ProcessingMode] = {}
    for raw_entity, raw_mode in policies.items():
        entity = _known_entity_type(raw_entity)
        if raw_mode == "inherit":
            continue
        try:
            mode = ProcessingMode(raw_mode)
        except (TypeError, ValueError) as error:
            raise ProcessingValidationError(f"Ungueltiger Modus fuer Policy {entity}.") from error
        if entity in normalized:
            raise ProcessingValidationError(f"Doppelte Policy fuer {entity}.")
        normalized[entity] = mode
    return MappingProxyType(dict(sorted(normalized.items())))


def _utf16_offset(text: str, python_offset: int) -> int:
    return len(text[:python_offset].encode("utf-16-le")) // 2


def _residual_risk_api_dict(result: ResidualRiskResult) -> dict[str, object]:
    status = "warning" if result.status is ResidualRiskStatus.FOUND else result.status.value
    return {
        "status": status,
        "heuristic": True,
        "threshold": result.threshold,
        "classes": [risk_class.value for risk_class in result.classes],
        "counts": {
            risk_class.value: result.counts[risk_class]
            for risk_class in ResidualRiskClass
            if result.counts[risk_class] > 0
        },
    }


# Precise name for later service wiring; keep the old name for readability in isolated unit tests.
ProcessingService = TextProcessor


__all__ = [
    "ENTITY_MINIMUM_LEVEL",
    "OFFSET_ENCODING",
    "AppliedFinding",
    "FindingKind",
    "ProcessingError",
    "ProcessingFacts",
    "ProcessingMode",
    "ProcessingReport",
    "ProcessingRequest",
    "ProcessingResult",
    "ProcessingService",
    "ProcessingValidationError",
    "ProtectionLevel",
    "ProtectionOverrides",
    "Reversibility",
    "ReviewAction",
    "ReviewDecision",
    "TextProcessor",
    "UnknownEntityTypeError",
    "UnresolvedPolicy",
    "finding_id",
    "minimum_protection_level",
]
