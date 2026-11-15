"""Detect language per paragraph.

A small, local model with a limited language set: For each line, count
function words of the enabled languages ("the", "and", "not"
against "the", "and", "not"), umlauts and ß count half for German. A
line is considered determined only if one language has at least 1.5 points
and a one-point lead. Short lines (table cells, "Invoice No. 4711") adopt the
language of the previous line; headings at the start of a paragraph take
the language of the following text; adjacent lines of the same language are
merged into a single segment.

Why no ready-made model (fastText ``lid.176``, lingua): Both come with
native dependencies or 30–120 MB to distinguish between two languages.
Measured against the corpus in ``tests/test_languages.py``, this method
covers every line with five words or more; with additional languages,
the choice must be remeasured.

The result contains no content, only ranges and language codes.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .models import LanguageSegment

_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?")
_NON_PROSE = re.compile(r"<[^<>\r\n]+>|(?:https?://|www\.)\S+|[^\s<>@]+@[^\s<>@]+", re.IGNORECASE)
_GERMAN_LETTERS = re.compile(r"[äöüßÄÖÜ]")
#: Minimum points and lead required for a line to be considered determined.
_MIN_SCORE = 1.5
_MIN_MARGIN = 1.0


@dataclass(frozen=True, slots=True)
class _Line:
    start: int
    end: int
    language: str | None


def score_languages(
    text: str,
    function_words: Mapping[str, frozenset[str]],
) -> dict[str, float]:
    """Points per language for a text segment."""

    # Technical citation headers ("DID", "AT"), URL and email components are
    # not function words. The cleaning applies only to scoring;
    # text and segment offsets remain unchanged.
    prose = _NON_PROSE.sub(" ", text)
    scores = dict.fromkeys(function_words, 0.0)
    for match in _WORD.finditer(prose):
        word = match.group().casefold()
        for language, words in function_words.items():
            if word in words:
                scores[language] += 1.0
    if "de" in scores and _GERMAN_LETTERS.search(prose):
        scores["de"] += 0.5
    return scores


def detect_language(
    text: str,
    function_words: Mapping[str, frozenset[str]],
) -> str | None:
    """Language of a text segment, or ``None`` if insufficient."""

    if len(function_words) == 1:
        return next(iter(function_words))
    scores = score_languages(text, function_words)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_language, best = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if best >= _MIN_SCORE and best - second >= _MIN_MARGIN:
        return best_language
    return None


def segment_languages(
    text: str,
    function_words: Mapping[str, frozenset[str]],
    *,
    default: str,
    per_paragraph: bool = True,
) -> tuple[LanguageSegment, ...]:
    """Split text into regions of the same language.

    ``function_words`` contains exactly the enabled languages. With one
    language or without ``per_paragraph``, a single segment is created for
    the entire document.
    """

    if not text:
        return ()
    if default not in function_words:
        raise ValueError("Die Vorgabesprache muss eingeschaltet sein.")
    if len(function_words) == 1:
        return (LanguageSegment(0, len(text), default, detected=False),)
    if not per_paragraph:
        language = detect_language(text, function_words)
        return (LanguageSegment(0, len(text), language or default, detected=language is not None),)

    lines = _lines(text, function_words)
    if all(line.language is None for line in lines):
        # No single paragraph is sufficient on its own – then the whole decides.
        language = detect_language(text, function_words)
        return (LanguageSegment(0, len(text), language or default, detected=language is not None),)
    return _merge(_inherit(text, lines))


def _lines(text: str, function_words: Mapping[str, frozenset[str]]) -> list[_Line]:
    lines: list[_Line] = []
    position = 0
    for match in re.finditer(r"\n", text):
        end = match.end()
        lines.append(_Line(position, end, detect_language(text[position:end], function_words)))
        position = end
    if position < len(text):
        lines.append(_Line(position, len(text), detect_language(text[position:], function_words)))
    return lines


def _inherit(text: str, lines: Sequence[_Line]) -> list[tuple[_Line, str, bool]]:
    """Undetermined lines inherit the language of their neighbors.

    Usually the previous line's language. An undetermined line at the start
    of a paragraph (after a blank line or at the very beginning) is usually
    a heading and belongs to the following text – it takes the language of
    the next determined line.
    """

    following: list[str | None] = [None] * len(lines)
    upcoming: str | None = None
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].language is not None:
            upcoming = lines[index].language
        following[index] = upcoming

    resolved: list[tuple[_Line, str, bool]] = []
    current: str | None = None
    paragraph_start = True
    for index, line in enumerate(lines):
        blank = not text[line.start : line.end].strip()
        if line.language is not None:
            current = line.language
            resolved.append((line, current, True))
        else:
            if (paragraph_start and not blank) or current is None:
                language = following[index] or current
            else:
                language = current
            assert language is not None
            if not blank and paragraph_start:
                current = language
            resolved.append((line, language, False))
        paragraph_start = blank
    return resolved


def _merge(resolved: Sequence[tuple[_Line, str, bool]]) -> tuple[LanguageSegment, ...]:
    segments: list[LanguageSegment] = []
    for line, language, detected in resolved:
        if segments and segments[-1].language == language:
            previous = segments[-1]
            segments[-1] = LanguageSegment(
                previous.start, line.end, language, detected=previous.detected or detected
            )
        else:
            segments.append(LanguageSegment(line.start, line.end, language, detected=detected))
    return tuple(segments)


def languages_present(segments: Sequence[LanguageSegment]) -> tuple[str, ...]:
    """Languages in order of their first occurrence."""

    return tuple(dict.fromkeys(segment.language for segment in segments))


__all__ = [
    "detect_language",
    "languages_present",
    "score_languages",
    "segment_languages",
]
