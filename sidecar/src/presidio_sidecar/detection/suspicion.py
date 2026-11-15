"""Pure CPU suspicion check for names missed by the NER."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from ..languages import DEFAULT_DOCUMENT_LANGUAGE
from .models import Finding, FindingSource
from .wordlists import (
    load_business_terms,
    load_sentence_starters,
    normalize_term,
    normalize_terms,
)

# Unicode letters, optionally with a hyphen or apostrophe inside the word.
_WORD_PATTERN = re.compile(r"(?<!\w)[^\W\d_]+(?:[-’'][^\W\d_]+)*(?!\w)")
_LINE_BREAKS = "\r\n\v\f\u2028\u2029"
_RUN_BREAKS = _LINE_BREAKS + "\t"
#: Legal forms in their usual spelling – "AG", not "Ag".
NAME_SUSPICION_DETECTOR = "capitalized_word_pair"
#: Legal forms in their usual spelling – “AG”, not “Ag”.
_LEGAL_FORMS = frozenset(
    {"GmbH", "mbH", "AG", "KG", "OHG", "UG", "SE", "KGaA", "Ltd", "Inc", "LLC", "PLC", "Corp"}
)
#: Word endings for banks and savings institutions ("Volksbank", "Sparkasse",
#: "Krankenkasse").
_ORGANIZATION_ENDINGS = ("bank", "kasse", "versicherung")


@dataclass(frozen=True, slots=True)
class _Word:
    text: str
    start: int
    end: int

    @property
    def is_capitalized(self) -> bool:
        letters = [character for character in self.text if character.isalpha()]
        return len(letters) >= 2 and letters[0].isupper()

    @property
    def is_uppercase(self) -> bool:
        return len(self.text) >= 2 and self.text.isupper()


def _is_covered(word: _Word, findings: tuple[Finding, ...]) -> bool:
    return any(word.start < finding.end and finding.start < word.end for finding in findings)


def _is_uppercase_attribute(run: list[_Word], words: tuple[_Word, ...], text: str) -> bool:
    """A run of uppercase letters among other uppercase words in its line.

    Product and position lines ("ANTHRAZIT,GESTE:METALL SCHWARZ") consist
    of uppercase terms; a name in uppercase usually stands alone
    ("MAX MUSTERMANN" in the address block or between em dashes).
    """

    if not all(word.is_uppercase for word in run):
        return False
    line_start = max((text.rfind(c, 0, run[0].start) for c in _LINE_BREAKS), default=-1) + 1
    ends = [position for c in _LINE_BREAKS if (position := text.find(c, run[-1].end)) != -1]
    line_end = min(ends, default=len(text))
    return any(
        word.is_uppercase and line_start <= word.start and word.end <= line_end
        for word in words
        if word.end <= run[0].start or word.start >= run[-1].end
    )


def _names_organization(run: list[_Word]) -> bool:
    """Names like „Volksbank Freiburg" and „Muster GmbH" refer to companies, not individuals."""

    return any(
        word.text in _LEGAL_FORMS or normalize_term(word.text).endswith(_ORGANIZATION_ENDINGS)
        for word in run
    )


def find_name_suspicions(
    text: str,
    covered_findings: Iterable[Finding] = (),
    *,
    sentence_starters: Iterable[str] | None = None,
    business_terms: Iterable[str] | None = None,
    score: float = 0.25,
    language: str = DEFAULT_DOCUMENT_LANGUAGE,
) -> tuple[Finding, ...]:
    """Marks runs consisting of at least two uncovered uppercase words.

    Business terms ("Online", "Team", "Zustellung") interrupt a
    run: "Ihr Online Team" is not a name, "Firma Hans Meier" remains one.
    """

    if not 0.0 <= score <= 1.0:
        raise ValueError("score muss zwischen 0 und 1 liegen")
    covered = tuple(covered_findings)
    stop_words = (
        load_sentence_starters(language=language)
        if sentence_starters is None
        else normalize_terms(sentence_starters)
    )
    terms = (
        load_business_terms(language=language)
        if business_terms is None
        else normalize_terms(business_terms)
    )
    words = tuple(
        _Word(match.group(), match.start(), match.end()) for match in _WORD_PATTERN.finditer(text)
    )

    runs: list[list[_Word]] = []
    current: list[_Word] = []
    for word in words:
        gap = text[current[-1].end : word.start] if current else ""
        follows_current = bool(
            current
            and current[-1].end <= word.start
            # Line/paragraph boundaries do not connect names to the next
            # heading or field label; tabs do not connect
            # table cells ("Variable\tBedeutung") or tab stops.
            and gap
            and gap.isspace()
            and not any(character in _RUN_BREAKS for character in gap)
        )
        eligible = (
            word.is_capitalized
            and normalize_term(word.text) not in terms
            and not _is_covered(word, covered)
        )
        if eligible and (not current or follows_current):
            current.append(word)
            continue
        if len(current) >= 2:
            runs.append(current)
        current = [word] if eligible else []
    if len(current) >= 2:
        runs.append(current)

    suspicious: list[Finding] = []
    for run in runs:
        # Only strip leading sentence starters. Discarding the whole run would hide the
        # unknown name that follows a stop word, e.g. in ``Bitte Gebrüder Wachtelhuber``.
        first_candidate = 0
        while (
            first_candidate < len(run) and normalize_term(run[first_candidate].text) in stop_words
        ):
            first_candidate += 1
        candidate_run = run[first_candidate:]
        if len(candidate_run) < 2:
            continue
        if _is_uppercase_attribute(candidate_run, words, text):
            continue
        start = candidate_run[0].start
        end = candidate_run[-1].end
        suspicious.append(
            Finding(
                entity_type="ORGANIZATION" if _names_organization(candidate_run) else "PERSON",
                start=start,
                end=end,
                score=score,
                source=FindingSource.SUSPICION,
                text=text[start:end],
                detector=NAME_SUSPICION_DETECTOR,
            )
        )
    return tuple(suspicious)
