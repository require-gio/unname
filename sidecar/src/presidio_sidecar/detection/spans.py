"""Handle offsets, overlaps, and replacements safely."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, replace

from .models import Finding

_BRACKET_PAIRS = {"(": ")", "[": "]", "{": "}"}
_CLOSING_BRACKETS = {closing: opening for opening, closing in _BRACKET_PAIRS.items()}


def _is_boundary_noise(character: str) -> bool:
    """Whitespace and Unicode punctuation, but no letters or digits."""

    return character.isspace() or unicodedata.category(character).startswith("P")


def trim_finding(text: str, finding: Finding) -> Finding | None:
    """Remove leading/trailing whitespace and punctuation, and correct offsets.

    Discard spans consisting entirely of such characters.
    """

    if finding.end > len(text):
        raise ValueError("Finding liegt ausserhalb des Textes")

    start = finding.start
    end = finding.end
    while start < end and _is_boundary_noise(text[start]):
        expected_closing = _BRACKET_PAIRS.get(text[start])
        if (
            finding.entity_type == "PHONE_NUMBER"
            and expected_closing is not None
            and expected_closing in text[start + 1 : end]
        ):
            break
        start += 1
    while end > start and _is_boundary_noise(text[end - 1]):
        expected_opening = _CLOSING_BRACKETS.get(text[end - 1])
        if (
            finding.entity_type == "PHONE_NUMBER"
            and expected_opening is not None
            and expected_opening in text[start : end - 1]
        ):
            break
        end -= 1
    if start == end:
        return None
    return replace(finding, start=start, end=end, text=text[start:end])


def trim_findings(text: str, findings: Iterable[Finding]) -> tuple[Finding, ...]:
    """Trims multiple findings and discards empty results."""

    trimmed: list[Finding] = []
    for finding in findings:
        candidate = trim_finding(text, finding)
        if candidate is not None:
            trimmed.append(candidate)
    return tuple(trimmed)


def spans_overlap(left: Finding, right: Finding) -> bool:
    """Half-open spans may touch but not intersect."""

    return left.start < right.end and right.start < left.end


def resolve_overlaps(findings: Iterable[Finding]) -> tuple[Finding, ...]:
    """Select the hit with the highest score for each overlap.

    On a tie, the longer span wins, then the earlier one. The return value is sorted by
    text position.
    """

    ranked = sorted(
        findings,
        key=lambda finding: (
            -finding.score,
            -finding.length,
            finding.start,
            finding.end,
            finding.entity_type,
        ),
    )
    selected: list[Finding] = []
    for candidate in ranked:
        if not any(spans_overlap(candidate, accepted) for accepted in selected):
            selected.append(candidate)
    return tuple(sorted(selected, key=lambda finding: (finding.start, finding.end)))


@dataclass(frozen=True, slots=True)
class SpanReplacement:
    """A replacement for exactly one original text span."""

    start: int
    end: int
    value: str

    @classmethod
    def for_finding(cls, finding: Finding, value: str) -> SpanReplacement:
        return cls(start=finding.start, end=finding.end, value=value)


def replace_back_to_front(text: str, replacements: Iterable[SpanReplacement]) -> str:
    """Replace from back to front so original offsets remain valid."""

    ordered = sorted(replacements, key=lambda item: (item.start, item.end), reverse=True)
    next_start = len(text)
    for item in ordered:
        if item.start < 0 or item.end <= item.start or item.end > len(text):
            raise ValueError("Ersatzspan liegt ausserhalb des Textes")
        if item.end > next_start:
            raise ValueError("Ueberlappende Ersatzspans sind nicht erlaubt")
        next_start = item.start

    result = text
    for item in ordered:
        result = result[: item.start] + item.value + result[item.end :]
    return result
