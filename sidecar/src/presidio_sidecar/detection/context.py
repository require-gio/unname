"""Field label before rule matches: context boosts, opposite context discards.

Presidio normally boosts matches via context words using NLP artifacts. Here the rules run without
them (``nlp_artifacts=None``), so the configured context lists are ineffective: every phone number
got the base score 0.4 and was treated as a question, even when directly after “Tel:".

This evaluation is intentionally lexical and narrow: it only looks at the few words before the value
on the same line – or the end of the preceding line if the value starts a new line (form and PDF
layouts).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import replace

from .models import Finding
from .wordlists import normalize_term

#: Words before the value that count as context.
CONTEXT_WINDOW = 3
#: Presidio default: nearby context adds this amount.
CONTEXT_BOOST = 0.35
#: A field label immediately before the value (“Tel: …”, “Steuernummer: …”)
#: makes the match definitive.
LABEL_SCORE = 0.95
#: Shorter context words count only as whole words; longer ones also match at the end
#: of a compound (“Kundentelefon”, “Auftragsdatum”).
_COMPOUND_MIN_LENGTH = 5

_TOKEN = re.compile(r"[^\W\d_]+")
_LINE_BREAKS = "\r\n\v\f  "
#: Between label and value allow whitespace and punctuation, but no digits or extra words.
_LABEL_GAP = re.compile(r"[\s:.#\-–/()]*")

_NUMBER_SUFFIXES = ("", "nr", "nummer", "no", "number")
#: Labels of foreign numbers. If one appears directly before the value, it is not a
#: phone or tax number but, for example, an order number.
_FOREIGN_NUMBER_STEMS = (
    "artikel",
    "art",
    "auftrag",
    "auftrags",
    "beleg",
    "bestell",
    "bestellung",
    "lieferschein",
    "pos",
    "position",
    "rechnung",
    "rechnungs",
    "sendung",
    "sendungs",
    "tracking",
    "ean",
    "sku",
    "article",
    "invoice",
    "item",
    "order",
)
FOREIGN_NUMBER_LABELS = frozenset(
    stem + suffix for stem in _FOREIGN_NUMBER_STEMS for suffix in _NUMBER_SUFFIXES
)
#: Types that a foreign number label disproves.
NUMBER_ENTITY_TYPES = frozenset({"PHONE_NUMBER", "DE_TAX_NUMBER", "DE_TAX_ID"})

#: German street suffixes – a closed word formation, not a list of individual values.
#: The address recognizer builds its pattern from these.
GERMAN_STREET_SUFFIXES = (
    "straße",
    "strasse",
    "str",
    "gasse",
    "weg",
    "platz",
    "allee",
    "ring",
    "damm",
    "ufer",
    "zeile",
    "steig",
    "kai",
    "lände",
    "laende",
    "promenade",
    "chaussee",
    "gürtel",
)
#: Street suffixes. A “Datum” without date context directly after a street name indicates
#: house number / stair / door (“Hauptstraße 12/1/30” (Main Street 12/1/30)).
_STREET_SUFFIXES = (*GERMAN_STREET_SUFFIXES, "street", "road", "avenue", "lane")
_CODE_CONTINUATION = re.compile(r"[/\-.]\d")

#: Types whose patterns may themselves be decimal or time forms.
_NUMBER_FRAGMENT_EXEMPT = frozenset({"DATE_TIME", "IP_ADDRESS"})
#: Decimal fraction with leading zero (“0.2817504”): after the leading 0 of a phone number
#: the area code always follows, never a separator.
_LEADING_ZERO_DECIMAL = re.compile(r"0[.,]\d+")


def is_number_fragment(text: str, finding: Finding) -> bool:
    """A digit value that is part of a decimal number or time.

    The patterns only stop at word characters; a period is a boundary.
    From “0.0308708” we get the phone number “0308708”, from
    “2.4388733333333334” a credit-card number, and from
    “2026-03-27 20:00:00” the SV number “2026-03-27 20” (regression in billing export).
    """

    value = text[finding.start : finding.end]
    if finding.entity_type in _NUMBER_FRAGMENT_EXEMPT or any(c.isalpha() for c in value):
        return False
    before = text[max(0, finding.start - 2) : finding.start]
    after = text[finding.end : finding.end + 2]
    return (
        (len(before) == 2 and before[0].isdigit() and before[1] in ".,:")
        or (len(after) == 2 and after[0] in ".,:" and after[1].isdigit())
        or bool(_LEADING_ZERO_DECIMAL.fullmatch(value))
    )


def apply_rule_context(
    text: str,
    finding: Finding,
    context: Iterable[str] | None,
) -> Finding | None:
    """Raise a rule match based on surrounding context or discard it (``None``).

    The score never decreases: without context the recognizer's base score stays.
    """

    if is_number_fragment(text, finding):
        return None
    tokens, label_tokens = _preceding_tokens(text, finding.start)
    label = "".join(label_tokens[-2:])
    if finding.entity_type in NUMBER_ENTITY_TYPES and (
        label in FOREIGN_NUMBER_LABELS
        or (label_tokens and label_tokens[-1] in FOREIGN_NUMBER_LABELS)
    ):
        return None

    words = tuple(_context_words(context or ()))
    immediate = any(_matches_at_end(label_tokens, word) for word in words)
    nearby = immediate or any(_matches_anywhere(tokens, word) for word in words)
    if immediate:
        score = min(1.0, max(finding.score + CONTEXT_BOOST, LABEL_SCORE))
    elif nearby:
        score = min(1.0, finding.score + CONTEXT_BOOST)
    else:
        if _contradicted_without_context(text, finding, label_tokens):
            return None
        return finding
    return replace(finding, score=max(finding.score, score))


def _contradicted_without_context(text: str, finding: Finding, label_tokens: Sequence[str]) -> bool:
    if finding.entity_type == "PHONE_NUMBER":
        # Part of a longer code like “04960008/02”: an article number.
        following = text[finding.end : finding.end + 2]
        preceding = text[finding.start - 1 : finding.start] if finding.start else ""
        return bool(_CODE_CONTINUATION.match(following)) or preceding.isalnum()
    if finding.entity_type == "DATE_TIME" and label_tokens:
        return label_tokens[-1].endswith(_STREET_SUFFIXES)
    return False


def _context_words(context: Iterable[str]) -> Iterable[tuple[str, ...]]:
    for word in context:
        parts = tuple(_TOKEN.findall(normalize_term(word)))
        if parts:
            yield parts


def _preceding_tokens(text: str, start: int) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The last words before ``start`` and the directly adjacent ones.

    If a line contains only punctuation before the value, treat the previous line's end as context
    because forms often place the label on the line above.
    """

    line_start = _line_start(text, start)
    before = text[line_start:start]
    if not _TOKEN.search(before) and line_start > 0:
        previous_start = _line_start(text, line_start - 1)
        before = text[previous_start : line_start - 1] + " " + before
    matches = list(_TOKEN.finditer(before))
    tokens = tuple(normalize_term(match.group()) for match in matches[-CONTEXT_WINDOW:])
    # Directly adjacent: only whitespace/punctuation up to the value, backwards
    # as long as the gaps are also just punctuation (“UID-Nr.:").
    adjacent: list[str] = []
    end = len(before)
    for match in reversed(matches):
        if not _LABEL_GAP.fullmatch(before[match.end() : end]):
            break
        adjacent.insert(0, normalize_term(match.group()))
        end = match.start()
        if len(adjacent) == CONTEXT_WINDOW:
            break
    return tokens, tuple(adjacent)


def _line_start(text: str, offset: int) -> int:
    return max((text.rfind(character, 0, offset) for character in _LINE_BREAKS), default=-1) + 1


def _token_matches(token: str, word: str) -> bool:
    return token == word or (len(word) >= _COMPOUND_MIN_LENGTH and token.endswith(word))


def _matches_at(tokens: Sequence[str], word: tuple[str, ...], end: int) -> bool:
    """``word`` ends at ``tokens[end - 1]`` – split or concatenated."""

    joined = "".join(word)
    if end >= 1 and len(word) > 1 and _token_matches(tokens[end - 1], joined):
        return True
    start = end - len(word)
    if start < 0:
        return False
    return _token_matches(tokens[start], word[0]) and all(
        tokens[start + index] == part for index, part in enumerate(word[1:], start=1)
    )


def _matches_at_end(tokens: Sequence[str], word: tuple[str, ...]) -> bool:
    if not tokens:
        return False
    if _matches_at(tokens, word, len(tokens)):
        return True
    # “UID-Nr.", “Tel.-Nr.": the identifier ends with a numeric suffix.
    return (
        len(tokens) >= 2
        and tokens[-1] in _NUMBER_SUFFIXES
        and _matches_at(tokens, word, len(tokens) - 1)
    )


def _matches_anywhere(tokens: Sequence[str], word: tuple[str, ...]) -> bool:
    return any(_matches_at(tokens, word, end) for end in range(1, len(tokens) + 1))


__all__ = [
    "CONTEXT_BOOST",
    "CONTEXT_WINDOW",
    "FOREIGN_NUMBER_LABELS",
    "GERMAN_STREET_SUFFIXES",
    "LABEL_SCORE",
    "NUMBER_ENTITY_TYPES",
    "apply_rule_context",
    "is_number_fragment",
]
