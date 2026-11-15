"""Common, framework-independent data types for the detection pipeline."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from .second_opinion import Assessment


class FindingSource(StrEnum):
    """The source stage from which a finding originates."""

    MASTER_DATA = "master_data"
    RULE = "rule"
    NER = "ner"
    SUSPICION = "suspicion"
    # Not a detection stage, but a manual entry from the review. It is intentionally in
    # the same data model: replacement, export, and review report must not distinguish
    # who found the location.
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class Finding:
    """A finding with Python offsets ``[start, end)`` in the original text."""

    entity_type: str
    start: int
    end: int
    score: float
    source: FindingSource | str
    text: str | None = None
    detector: str | None = None

    def __post_init__(self) -> None:
        entity_type = self.entity_type.strip().upper()
        if not entity_type:
            raise ValueError("entity_type darf nicht leer sein")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("Ein Finding braucht einen nicht-leeren, positiven Span")
        if not math.isfinite(self.score) or not 0.0 <= self.score <= 1.0:
            raise ValueError("score muss zwischen 0 und 1 liegen")

        object.__setattr__(self, "entity_type", entity_type)
        try:
            object.__setattr__(self, "source", FindingSource(self.source))
        except ValueError:
            # External/incoming recognizers may name their source more precisely without
            # requiring changes to the central data model.
            object.__setattr__(self, "source", str(self.source))

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class LanguageSegment:
    """A contiguous text range in a document language.

    The segments of a detection lie back-to-back without gaps or overlaps and cover the
    entire text. ``detected`` is ``False`` if the range was too short for an independent
    decision and the language was adopted from its neighbors (or the default).
    """

    start: int
    end: int
    language: str
    detected: bool = True

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError("Ein Sprachsegment braucht einen gueltigen Bereich")


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """Result with deliberately separated review categories."""

    confirmed: tuple[Finding, ...] = ()
    suspicious: tuple[Finding, ...] = ()
    #: Document language per range; empty for detections without language reference
    #: (legacy UI, tests).
    languages: tuple[LanguageSegment, ...] = ()
    #: Assessment by the local AI per finding ID of a question; empty if the second
    #: opinion is off or its model is missing.
    assessments: Mapping[str, Assessment] = field(default_factory=dict)

    def language_at(self, offset: int, default: str | None = None) -> str | None:
        """Language at a position in the source text."""

        for segment in self.languages:
            if segment.start <= offset < segment.end:
                return segment.language
        if self.languages and offset >= self.languages[-1].end:
            return self.languages[-1].language
        return default

    @property
    def all_findings(self) -> tuple[Finding, ...]:
        return self.confirmed + self.suspicious
