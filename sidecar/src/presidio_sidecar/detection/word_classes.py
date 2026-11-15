"""Word classes as name markers: proper noun, common noun, or pronoun.

In German every noun is capitalized, and GLiNER's label "person"
means any *mention* of a person, not just a *name*. In an insurance policy it therefore reports
"Verbraucher" (consumer) with 0.99, "der Versicherungsnehmer" (the policyholder) with 0.93 and "Sie"
(you) with up to 0.97 – a threshold
does not separate them,
and any word list would be incomplete after the next document.

A word-class tagger is trained for this distinction (TIGER:
``NE`` proper noun vs ``NN`` common noun, ``PPER`` personal pronoun; Penn for English).
It is already on the device with the language pack and tags a document in fractions of a second.

Measured on the second-opinion corpus (311 documents, 2058 correct
name hits from GLiNER, 137 false):

* A hit **consisting only of pronouns, articles and particles** was never a name
  (0 of 2058) – it is discarded.
* A hit **without a proper-noun tag** is often still a name: the small tagger treats rare surnames
  (“Kranebitter”, “Wachtelhuber”) as common nouns (113 of 2058). The tagger alone must not decide.
  Only when the second opinion also confirms the span is a term is the hit removed – two independent
  signals.
  Result: 0 of 2058 names and 0 of 1949 companies lost; in the never-trained test part 0 of 465 and
  0 of 430.

If the language pack or the second opinion is missing, the step is skipped and the hit remains
as-is: mask it when in doubt.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Sequence
from enum import StrEnum
from typing import Any, Protocol

from .models import Finding, FindingSource, LanguageSegment

#: Types where a word class says something about the hit. Locations are often
#: common nouns with name force ("Hauptbahnhof"); there the corpus review
#: has not improved anything and is skipped.
CHECKED_TYPES = frozenset({"PERSON", "ORGANIZATION"})
PROPER_TAGS = frozenset({"NE", "FM", "XY", "NNP", "NNPS"})
#: Closed word classes: pronouns, articles, prepositions, conjunctions,
#: adverbs (STTS and Penn). A name never consists solely of these. This
#: exact set is measured; anyone extending it must re-measure.
CLOSED_TAGS = frozenset(
    {
        "PPER", "PRF", "PPOSAT", "PPOSS", "PDS", "PDAT", "PIS", "PIAT", "PIDAT",
        "PRELS", "PRELAT", "PWS", "PWAT", "ART", "KON", "APPR", "APPRART", "ADV",
        "PTKVZ",
        "PRP", "PRP$", "DT", "WP", "WP$", "IN", "CC",
    }
)  # fmt: skip
#: Context characters per page used to tag a finding: enough for the sentence,
#: without tagging the entire document in large files.
CONTEXT_CHARACTERS = 160

#: (Start, End, Tag) per token, offsets relative to the tagged snippet.
TaggedTokens = Sequence[tuple[int, int, str]]


class WordClass(StrEnum):
    PROPER = "proper"
    COMMON = "common"
    CLOSED = "closed"


class WordClassTagger(Protocol):
    """Tags excerpts of a language; ``None`` if no model exists."""

    def tag(self, texts: Sequence[str], language: str) -> Sequence[TaggedTokens] | None: ...


#: Asks the second opinion for each hit whether it is *certain* that it is a
#: term (``True``: do not mask). Returning empty/too short means: no.
TermCheck = Callable[[str, Sequence[Finding]], Sequence[bool]]


def word_class(text: str, tokens: TaggedTokens, start: int, end: int) -> WordClass | None:
    """Word class of a span based on the tags of its tokens (offsets like ``tokens``)."""

    tags = [
        tag
        for token_start, token_end, tag in tokens
        if token_start < end
        and start < token_end
        and any(character.isalpha() for character in text[token_start:token_end])
    ]
    if not tags:
        return None
    if all(tag in CLOSED_TAGS for tag in tags):
        return WordClass.CLOSED
    if any(tag in PROPER_TAGS for tag in tags):
        return WordClass.PROPER
    return WordClass.COMMON


def classify_findings(
    text: str,
    findings: Sequence[Finding],
    tagger: WordClassTagger,
    *,
    language_at: Callable[[int], str],
) -> dict[int, WordClass]:
    """Word class per index in ``findings``; hits without a model are missing
    from the result.

    Tagging is done per language only for the context of the hits, aggregated
    windows at whitespace boundaries.
    """

    by_language: dict[str, list[int]] = {}
    for index, finding in enumerate(findings):
        by_language.setdefault(language_at(finding.start), []).append(index)
    result: dict[int, WordClass] = {}
    for language, indices in by_language.items():
        windows = _windows(text, (findings[index] for index in indices))
        tagged = tagger.tag([text[start:end] for start, end in windows], language)
        if tagged is None:
            continue
        for index in indices:
            finding = findings[index]
            for (window_start, window_end), tokens in zip(windows, tagged, strict=True):
                if window_start <= finding.start and finding.end <= window_end:
                    verdict = word_class(
                        text[window_start:window_end],
                        tokens,
                        finding.start - window_start,
                        finding.end - window_start,
                    )
                    if verdict is not None:
                        result[index] = verdict
                    break
    return result


def without_non_names(
    text: str,
    findings: Iterable[Finding],
    tagger: WordClassTagger,
    *,
    language_at: Callable[[int], str],
    term_check: TermCheck | None = None,
) -> tuple[Finding, ...]:
    """Discards NER persons and companies that are not names by word class.

    Pronouns and articles are always discarded. Common nouns without proper
    nouns are discarded only if ``term_check`` (the second opinion) reads
    them as a term with certainty.
    All other hits remain unchanged.
    """

    ordered = tuple(findings)
    candidates = [
        index
        for index, finding in enumerate(ordered)
        if finding.source == FindingSource.NER and finding.entity_type in CHECKED_TYPES
    ]
    if not candidates:
        return ordered
    verdicts = classify_findings(
        text, [ordered[index] for index in candidates], tagger, language_at=language_at
    )
    dropped: set[int] = set()
    common: list[int] = []
    for position, index in enumerate(candidates):
        verdict = verdicts.get(position)
        if verdict is WordClass.CLOSED:
            dropped.add(index)
        elif verdict is WordClass.COMMON:
            common.append(index)
    if common and term_check is not None:
        answers = tuple(term_check(text, [ordered[index] for index in common]))
        dropped.update(index for index, is_term in zip(common, answers, strict=False) if is_term)
    return tuple(finding for index, finding in enumerate(ordered) if index not in dropped)


def _windows(text: str, findings: Iterable[Finding]) -> list[tuple[int, int]]:
    spans = sorted((finding.start, finding.end) for finding in findings)
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        left = _whitespace_before(text, max(0, start - CONTEXT_CHARACTERS))
        right = _whitespace_after(text, min(len(text), end + CONTEXT_CHARACTERS))
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        else:
            merged.append((left, right))
    return merged


def _whitespace_before(text: str, offset: int) -> int:
    while offset > 0 and not text[offset - 1].isspace():
        offset -= 1
    return offset


def _whitespace_after(text: str, offset: int) -> int:
    while offset < len(text) and not text[offset].isspace():
        offset += 1
    return offset


class SpacyWordClassTagger:
    """The tagger from installed spaCy language packages, loaded lazily per language.

    Only ``tok2vec`` and ``tagger`` are loaded – no parser, no NER.
    ``locate`` returns the model path per language or ``None``.
    """

    #: What the tagger does not need. ``exclude`` does not load these parts at all.
    _EXCLUDE = ("parser", "senter", "ner", "lemmatizer", "attribute_ruler", "morphologizer")

    def __init__(
        self,
        locate: Callable[[str], str | None],
        *,
        loader: Callable[[str], Any] | None = None,
    ) -> None:
        self._locate = locate
        self._loader = loader or self._load
        self._models: dict[str, Any] = {}
        self._missing: dict[str, str | None] = {}
        self._lock = threading.Lock()

    @classmethod
    def _load(cls, reference: str) -> Any:
        import spacy

        return spacy.load(reference, exclude=list(cls._EXCLUDE))

    def _model(self, language: str) -> Any | None:
        reference = self._locate(language)
        if reference is None:
            return None
        with self._lock:
            model = self._models.get(language)
            if model is not None:
                return model
            if self._missing.get(language, "") == reference:
                return None
            try:
                model = self._loader(reference)
            except Exception:  # noqa: BLE001 – A missing package means no document.
                self._missing[language] = reference
                return None
            self._models[language] = model
            return model

    def tag(self, texts: Sequence[str], language: str) -> list[list[tuple[int, int, str]]] | None:
        model = self._model(language)
        if model is None:
            return None
        return [
            [(token.idx, token.idx + len(token.text), token.tag_) for token in document]
            for document in model.pipe(texts)
        ]


def language_lookup(segments: Sequence[LanguageSegment], default: str) -> Callable[[int], str]:
    """Determine the language at a text position from the segments of a detection."""

    def at(offset: int) -> str:
        for segment in segments:
            if segment.start <= offset < segment.end:
                return segment.language
        return segments[-1].language if segments else default

    return at


__all__ = [
    "CHECKED_TYPES",
    "CLOSED_TAGS",
    "PROPER_TAGS",
    "SpacyWordClassTagger",
    "TermCheck",
    "WordClass",
    "WordClassTagger",
    "classify_findings",
    "language_lookup",
    "without_non_names",
    "word_class",
]
