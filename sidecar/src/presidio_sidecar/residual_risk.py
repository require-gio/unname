"""Heuristic residual risk review on the already cleaned text."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from .vault import normalize_entity_type


class ResidualRiskStatus(StrEnum):
    CLEAR = "clear"
    FOUND = "warning"
    NOT_RUN = "not_run"
    FAILED = "failed"


class ResidualRiskClass(StrEnum):
    DATE_TIME = "date_time"
    AMOUNT_QUANTITY = "amount_quantity"
    REFERENCE_NUMBER = "reference_number"
    LOCATION_REGION = "location_region"
    RARE_TERM = "rare_term"


CLASS_LABELS: Final[Mapping[ResidualRiskClass, str]] = MappingProxyType(
    {
        ResidualRiskClass.DATE_TIME: "Datums- und Zeitangaben",
        ResidualRiskClass.AMOUNT_QUANTITY: "Beträge und Mengen",
        ResidualRiskClass.REFERENCE_NUMBER: "Artikel-, Vertrags- und Vorgangsnummern",
        ResidualRiskClass.LOCATION_REGION: "Orts- und Regionsangaben",
        ResidualRiskClass.RARE_TERM: "Seltene Fachbegriffe oder Berufsbezeichnungen",
    }
)

HEURISTIC_NOTICE: Final = (
    "Heuristische Restrisiko-Prüfung: Die gefundenen Merkmalsklassen können "
    "eine Person eingrenzen. Die Prüfung ersetzt keine menschliche Bewertung."
)

_SOFTWARE_VERSION = re.compile(
    r"(?<!\w)(?:Version|Release|v)\s*\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?(?!\w)",
    re.IGNORECASE,
)

_DATE_TIME_PATTERNS = (
    re.compile(
        r"(?<!\w)(?:0?[1-9]|[12]\d|3[01])[./-]"
        r"(?:0?[1-9]|1[0-2])[./-](?:19|20)?\d{2}(?!\w)"
    ),
    re.compile(
        r"(?<!\w)(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-"
        r"(?:0[1-9]|[12]\d|3[01])(?:[T ](?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?)?(?!\w)"
    ),
    re.compile(r"(?<!\w)(?:[01]?\d|2[0-3]):[0-5]\d(?:\s*Uhr)?(?!\w)", re.IGNORECASE),
    re.compile(
        r"(?<!\w)(?:0?[1-9]|[12]\d|3[01])\.?\s+"
        r"(?:Januar|Februar|März|April|Mai|Juni|Juli|August|September|"
        r"Oktober|November|Dezember)\s+(?:19|20)\d{2}(?!\w)",
        re.IGNORECASE,
    ),
)

_AMOUNT_QUANTITY_PATTERNS = (
    re.compile(
        r"(?<!\w)(?:\d{1,3}(?:[.\s]\d{3})*|\d+)(?:,\d{1,2})?"
        r"\s*(?:€|EUR|USD|CHF)(?!\w)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<!\w)\d+(?:[.,]\d+)?\s*"
        r"(?:Stück|Stk\.?|kg|Kilogramm|g|Gramm|t|Tonnen?|l|Liter|"
        r"ml|m|Meter|km|Kilometer|m²|m3|Pakete?|Paletten?|Container)(?!\w)",
        re.IGNORECASE,
    ),
)

_REFERENCE_NUMBER_PATTERNS = (
    re.compile(
        r"(?<!\w)(?:Artikel|Art\.?|Vertrag|Vorgang|Akte|Aktenzeichen|"
        r"Rechnung|Auftrag|Bestellung|Sendung|Ticket)"
        r"(?:[\s-]*(?:nummer|nr\.?|zeichen|id))?\s*[:#]?\s*"
        r"(?=[A-Z0-9./-]{3,}\b)(?=[A-Z0-9./-]*\d)[A-Z0-9][A-Z0-9./-]*",
        re.IGNORECASE,
    ),
)

_KNOWN_LOCATIONS = (
    "Wien",
    "Graz",
    "Linz",
    "Salzburg",
    "Innsbruck",
    "Klagenfurt",
    "Bregenz",
    "Eisenstadt",
    "St. Pölten",
    "Österreich",
    "Deutschland",
    "Berlin",
    "Hamburg",
    "München",
    "Köln",
    "Frankfurt",
    "Stuttgart",
    "Düsseldorf",
    "Bayern",
    "Sachsen",
    "Tirol",
    "Vorarlberg",
    "Burgenland",
    "Steiermark",
    "Kärnten",
    "Hallstatt",
    "Kufstein",
    "Leoben",
    "Villach",
    "Wels",
    "Wiener Neustadt",
    "Passau",
    "Regensburg",
    "Nürnberg",
    "Augsburg",
    "Freiburg",
    "Heidelberg",
    "Potsdam",
    "Rostock",
    "Bremen",
    "Hannover",
    "Leipzig",
    "Dresden",
)
_LOCATION_PATTERNS = (
    re.compile(
        rf"(?<!\w)(?:{'|'.join(re.escape(value) for value in _KNOWN_LOCATIONS)})(?!\w)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<!\w)(?:PLZ|Postleitzahl)\s*:?\s*\d{4,5}(?!\w)",
        re.IGNORECASE,
    ),
    re.compile(r"(?<!\w)(?:A-|D-)\d{4,5}\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß.' -]{2,40}(?!\w)"),
)

_RARE_TERM_PATTERNS = (
    re.compile(
        r"(?<!\w)(?:Geschäftsführer(?:in)?|Prokurist(?:in)?|"
        r"Rechtsanwältin|Rechtsanwalt|Notarin|Notar|Kardiologin|Kardiologe|"
        r"Neurochirurgin|Neurochirurg|"
        r"Zolltarifnummer|Gefahrgutbeauftragte[rs]?|"
        r"Schweißfachingenieur(?:in)?|Datenschutzbeauftragte[rs]?)(?!\w)",
        re.IGNORECASE,
    ),
)

_ENTITY_CLASS = {
    "DATE_TIME": ResidualRiskClass.DATE_TIME,
    "LOCATION": ResidualRiskClass.LOCATION_REGION,
    "DE_PLZ": ResidualRiskClass.LOCATION_REGION,
    "ADDRESS": ResidualRiskClass.LOCATION_REGION,
    "STREET_ADDRESS": ResidualRiskClass.LOCATION_REGION,
    "NRP": ResidualRiskClass.RARE_TERM,
    "JOB_TITLE": ResidualRiskClass.RARE_TERM,
    "PROFESSION": ResidualRiskClass.RARE_TERM,
    "OTHER": ResidualRiskClass.RARE_TERM,
}


@dataclass(frozen=True, slots=True)
class ResidualRiskResult:
    status: ResidualRiskStatus
    classes: tuple[ResidualRiskClass, ...]
    counts: Mapping[ResidualRiskClass, int]
    threshold: int
    kind: str = "heuristic"
    blocks_export: bool = False
    notice: str = HEURISTIC_NOTICE

    @property
    def found(self) -> bool:
        return self.status is ResidualRiskStatus.FOUND

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "kind": self.kind,
            "heuristic": True,
            "classes": list(self.classes),
            "counts": {risk_class: self.counts[risk_class] for risk_class in self.classes},
            "class_labels": {risk_class: CLASS_LABELS[risk_class] for risk_class in self.classes},
            "class_count": len(self.classes),
            "threshold": self.threshold,
            "found": self.found,
            "blocks_export": self.blocks_export,
            "notice": self.notice,
        }


class ResidualRiskAnalyzer:
    def __init__(self, *, warn_at_classes: int = 2) -> None:
        if warn_at_classes < 1:
            raise ValueError("Restrisiko-Schwelle muss mindestens 1 sein.")
        self.warn_at_classes = warn_at_classes

    def analyze(
        self,
        cleaned_text: str,
        *,
        remaining_entity_types: Iterable[str] = (),
        replacement_spans: Iterable[tuple[int, int]] = (),
    ) -> ResidualRiskResult:
        """Count feature classes, not the actual confidential values."""

        sanitized = _SOFTWARE_VERSION.sub(
            " ",
            _blank_spans(cleaned_text, replacement_spans),
        )
        counts: dict[ResidualRiskClass, int] = {
            ResidualRiskClass.DATE_TIME: _count_matches(sanitized, _DATE_TIME_PATTERNS),
            ResidualRiskClass.AMOUNT_QUANTITY: _count_matches(sanitized, _AMOUNT_QUANTITY_PATTERNS),
            ResidualRiskClass.REFERENCE_NUMBER: _count_matches(
                sanitized, _REFERENCE_NUMBER_PATTERNS
            ),
            ResidualRiskClass.LOCATION_REGION: _count_matches(sanitized, _LOCATION_PATTERNS),
            ResidualRiskClass.RARE_TERM: _count_matches(sanitized, _RARE_TERM_PATTERNS),
        }
        for raw_entity_type in remaining_entity_types:
            try:
                entity_type = normalize_entity_type(raw_entity_type)
            except (TypeError, ValueError):
                continue
            risk_class = _ENTITY_CLASS.get(entity_type)
            if risk_class is not None:
                counts[risk_class] = max(1, counts[risk_class])

        classes = tuple(risk_class for risk_class in ResidualRiskClass if counts[risk_class] > 0)
        status = (
            ResidualRiskStatus.FOUND
            if len(classes) >= self.warn_at_classes
            else ResidualRiskStatus.CLEAR
        )
        return ResidualRiskResult(
            status=status,
            classes=classes,
            counts=MappingProxyType(counts),
            threshold=self.warn_at_classes,
        )


def _count_matches(text: str, patterns: Iterable[re.Pattern[str]]) -> int:
    return sum(len(pattern.findall(text)) for pattern in patterns)


def _blank_spans(text: str, spans: Iterable[tuple[int, int]]) -> str:
    """Hide only the replacements that the processor made, not guessed values."""

    ordered = sorted(spans)
    previous_end = 0
    pieces: list[str] = []
    for start, end in ordered:
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start < previous_end
            or end <= start
            or end > len(text)
        ):
            raise ValueError("Ungueltiger oder ueberlappender Ersatzspan.")
        pieces.append(text[previous_end:start])
        pieces.append(" " * (end - start))
        previous_end = end
    pieces.append(text[previous_end:])
    return "".join(pieces)
