"""MaskingSpec (``MaskingSpec``) and everything derived from it.

The UI now uses method (reversible / irreversible), a replacement style, and selected categories.
This module translates the spec into the processing steps from ``processing.py``:

* style → processing mode for all types,
* unselected categories → ``keep_plain`` for those types,
* method → ``vault_reversible``,
* the level is only derived for audit and review reports.

“Anonymize” is derived (irreversible ∧ all categories). The client sends the flag, the sidecar
validates it:
a spec that claims “anonymous” without meeting the criteria is rejected rather than silently
corrected.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .catalog import (
    CATEGORIES,
    CATEGORIES_BY_ID,
    CATEGORY_BY_ENTITY,
    CATEGORY_IDS,
    STYLE_MODES,
    STYLES,
    default_category_ids,
    locked_category_ids,
)
from .processing import (
    ENTITY_MINIMUM_LEVEL,
    ManualFinding,
    ProcessingMode,
    ProcessingRequest,
    ProcessingResult,
    ProtectionLevel,
    ProtectionOverrides,
    ReviewDecision,
    UnresolvedPolicy,
)

ResultLabel = Literal["reversible", "irreversible", "anonymous"]
AnonymousBlocker = Literal[
    "not_requested",
    "modes",
    "kept_here",
    "plaintext",
    "residual_risk",
    "nothing_masked",
]


class MaskingSpec(BaseModel):
    """The masking spec – identical in frontend and sidecar."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    method: Literal["reversible", "irreversible"]
    style: Literal["codes", "surrogates", "placeholder", "mask", "remove"]
    categories: tuple[str, ...] = Field(
        description="Kategorie-IDs aus GET /catalog; gesperrte sind immer dabei."
    )
    anonymize: bool = Field(
        default=False,
        description="Abgeleitet: irreversibel und alle Kategorien. Wird geprueft, nie geraten.",
    )

    @field_validator("categories")
    @classmethod
    def _normalized_categories(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = [item for item in value if item not in CATEGORIES_BY_ID]
        if unknown:
            raise ValueError("Unbekannte Kategorie: " + ", ".join(sorted(set(unknown))))
        # Preserve catalog order; locked categories are always included.
        # All specs look the same and cannot exclude account or ID data.
        chosen = set(value) | set(locked_category_ids())
        return tuple(item for item in CATEGORY_IDS if item in chosen)

    @model_validator(mode="after")
    def _consistent(self) -> MaskingSpec:
        if self.style not in STYLES[self.method]:
            raise ValueError(f"Stil {self.style!r} passt nicht zur Methode {self.method!r}.")
        if self.anonymize != derived_anonymize(self.method, self.categories):
            raise ValueError(
                "anonymize passt nicht zum Auftrag: Es gilt genau bei irreversibler "
                "Methode mit allen Kategorien."
            )
        return self

    @classmethod
    def default(cls) -> MaskingSpec:
        return cls(
            method="reversible",
            style=STYLES["reversible"][0],  # type: ignore[arg-type]
            categories=default_category_ids(),
        )

    @property
    def mode(self) -> ProcessingMode:
        return ProcessingMode(STYLE_MODES[self.style])

    @property
    def vault_reversible(self) -> bool:
        return self.method == "reversible"

    @property
    def handled_entity_types(self) -> frozenset[str]:
        return frozenset(
            entity for category_id in self.categories for entity in _types_of(category_id)
        )

    @property
    def kept_plain_entity_types(self) -> frozenset[str]:
        return frozenset(ENTITY_MINIMUM_LEVEL) - self.handled_entity_types

    @property
    def protection_level(self) -> ProtectionLevel:
        """Highest level whose categories are fully selected.

        Only used for audit and review reports. Level 1 is always active
        because its category is locked.
        """

        chosen = set(self.categories)
        level = ProtectionLevel.CRITICAL
        for candidate in (ProtectionLevel.DIRECT, ProtectionLevel.INDIRECT):
            required = {category.id for category in CATEGORIES if category.level <= candidate}
            if required <= chosen:
                level = candidate
        return level

    def with_categories(self, categories: Iterable[str]) -> MaskingSpec:
        chosen = tuple(categories)
        return MaskingSpec(
            method=self.method,
            style=self.style,
            categories=chosen,
            anonymize=derived_anonymize(self.method, chosen),
        )


def derived_anonymize(method: str, categories: Iterable[str]) -> bool:
    chosen = set(categories) | set(locked_category_ids())
    return method == "irreversible" and chosen >= set(CATEGORY_IDS)


def _types_of(category_id: str) -> tuple[str, ...]:
    return CATEGORIES_BY_ID[category_id].entity_types


def processing_request(
    spec: MaskingSpec,
    *,
    decisions: Iterable[ReviewDecision] = (),
    manual_findings: Iterable[ManualFinding] = (),
    unresolved: UnresolvedPolicy = UnresolvedPolicy.MASK,
    question_ids: Iterable[str] = (),
    placeholder_language: str = "de",
    placeholder_languages: Iterable[tuple[int, int, str]] = (),
) -> ProcessingRequest:
    """Translate a request into the internal processing model.

    The level is set to 3 so that only ``keep_plain`` decides what stays in
    clear text. This makes a category affect exactly its entity types,
    regardless of its original level.
    """

    return ProcessingRequest(
        mode=spec.mode,
        protection_level=ProtectionLevel.INDIRECT,
        overrides=ProtectionOverrides(keep_plain=spec.kept_plain_entity_types),
        vault_reversible=spec.vault_reversible,
        decisions=tuple(decisions),
        manual_findings=tuple(manual_findings),
        unresolved=unresolved,
        question_ids=frozenset(question_ids),
        placeholder_language=placeholder_language,
        placeholder_languages=tuple(placeholder_languages),
    )


def label_facts(spec: MaskingSpec, result: ProcessingResult) -> dict[str, Any]:
    """Common rule for all views.

    “Anonym” applies only when all conditions are met: the request requires it,
    only anonymisation-eligible modes are used (no plain-text exceptions such as
    “keep only here” or “keep”), and the residual-risk check yields no findings.
    Missing conditions are listed in ``anonymous_blockers``; the UI explains
    why masking is not anonymous.
    """

    facts = result.facts
    report = result.report
    blockers: list[AnonymousBlocker] = []
    if not spec.anonymize:
        blockers.append("not_requested")
    if not result.applied_findings:
        blockers.append("nothing_masked")
    elif not facts.only_anonymization_eligible_modes:
        blockers.append("modes")
    if sum(report.rejected_counts_by_entity.values()):
        blockers.append("kept_here")
    if facts.has_plaintext_overrides:
        blockers.append("plaintext")
    if not facts.residual_clear:
        blockers.append("residual_risk")

    label: ResultLabel
    if spec.method == "reversible":
        label = "reversible"
    elif blockers:
        label = "irreversible"
    else:
        label = "anonymous"
    return {
        "label": label,
        "contains_surrogates": facts.contains_surrogates,
        "anonymous_blockers": blockers if spec.method == "irreversible" else ["not_requested"],
        "protection_level": int(spec.protection_level),
        "kept_here_count": sum(report.rejected_counts_by_entity.values()),
        "masked_without_decision": report.unresolved_masked_count,
    }


def counts_by_category(counts_by_entity: Mapping[str, int]) -> dict[str, int]:
    """Count per category in catalog order, excluding empty entries."""

    totals = dict.fromkeys(CATEGORY_IDS, 0)
    for entity, count in counts_by_entity.items():
        category = CATEGORY_BY_ENTITY.get(entity)
        if category is not None:
            totals[category.id] += count
    return {category: count for category, count in totals.items() if count}


__all__ = [
    "AnonymousBlocker",
    "MaskingSpec",
    "ResultLabel",
    "counts_by_category",
    "derived_anonymize",
    "label_facts",
    "processing_request",
]
