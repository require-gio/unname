"""Composable plain-text pipeline for stages 2 and 3."""

from __future__ import annotations

import unicodedata
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping
from time import monotonic

import regex

from ..languages import DEFAULT_DOCUMENT_LANGUAGE
from .models import DetectionResult, Finding, FindingSource, LanguageSegment
from .ner import (
    EntityThresholds,
    NerAdapter,
    build_ner_adapter,
    detect_with_segments,
    filter_ner_findings,
)
from .spans import resolve_overlaps, trim_finding
from .suspicion import find_name_suspicions
from .word_classes import TermCheck, WordClassTagger, language_lookup, without_non_names
from .wordlists import load_business_terms, load_sentence_starters, normalize_term, normalize_terms

EXACT_DUPLICATE_DETECTOR = "exact_duplicate"
_MAX_DUPLICATE_PATTERNS = 10_000
_MAX_DUPLICATE_PATTERN_CHARACTERS = 2_000_000
_MAX_DUPLICATE_PREFIX_DEPTH = 32
_MAX_DUPLICATE_CANDIDATES = 50_000
_MAX_DUPLICATE_PASSES = 32
_DUPLICATE_MATCH_TIMEOUT_SECONDS = 2.0


class DuplicateReviewCapacityError(RuntimeError):
    """Raise this error when too many skipped repetitions would exceed the capacity for
    position-accurate review.
    position-accurate review.
    """


class DetectionPipeline:
    """Enrich confirmed rule- and master-data hits with NER and suspicion.

    ``language`` selects the word lists for calls without language-specific
    lists. Model selection and paragraph languages follow the ``languages`` segments.

    ``word_classes`` checks NER persons and companies for their word class
    (``word_classes.py``): pronouns are dropped, common nouns only with the
    ``term_check`` of the second opinion. Without a tagger, everything remains as
    detected.
    """

    def __init__(
        self,
        ner: NerAdapter | None = None,
        *,
        thresholds: EntityThresholds | None = None,
        business_terms: Iterable[str] | None = None,
        sentence_starters: Iterable[str] | None = None,
        language: str = DEFAULT_DOCUMENT_LANGUAGE,
        word_classes: WordClassTagger | None = None,
    ) -> None:
        self.language = language
        self.word_classes = word_classes
        self.thresholds = thresholds or EntityThresholds()
        self.ner = ner or build_ner_adapter(thresholds=self.thresholds)
        self.business_terms = (
            load_business_terms(language=language)
            if business_terms is None
            else normalize_terms(business_terms)
        )
        self.sentence_starters = (
            load_sentence_starters(language=language)
            if sentence_starters is None
            else normalize_terms(sentence_starters)
        )

    def analyze(
        self,
        text: str,
        *,
        confirmed: Iterable[Finding] = (),
        progress: Callable[[str], None] | None = None,
        allowed_entities: Iterable[str] | None = None,
        thresholds: EntityThresholds | None = None,
        suspicion_enabled: bool = True,
        sentence_starters: Iterable[str] | None = None,
        on_resolved: Callable[[tuple[Finding, ...]], None] | None = None,
        languages: tuple[LanguageSegment, ...] = (),
        sentence_starters_by_language: Mapping[str, Iterable[str]] | None = None,
        business_terms: Iterable[str] | None = None,
        business_terms_by_language: Mapping[str, Iterable[str]] | None = None,
        language_thresholds: Mapping[str, EntityThresholds] | None = None,
        term_check: TermCheck | None = None,
    ) -> DetectionResult:
        """Runs NER, post-filtering, span resolution, and suspicion review.

        ``on_resolved`` receives the confirmed hits after NER, before
        suspicion review runs – for the UI's intermediate count ("found so far").

        Since Phase J: ``languages`` splits the text into document languages. The
        spaCy fallback uses the model for each segment's language, suspicion review
        uses the sentence starters for that language
        (``sentence_starters_by_language``), and ``language_thresholds`` calibrates
        NER thresholds per language. Business terms are also selected per segment
        from ``business_terms_by_language``.
        """

        allowed = (
            {entity.strip().upper() for entity in allowed_entities}
            if allowed_entities is not None
            else None
        )
        # Master data and rule values are exact values and may contain
        # relevant punctuation (``e.U.``, Secret-``!``). Only the
        # PhoneRecognizer sometimes cuts a single wrapper parenthesis;
        # we normalize this known edge case specifically.
        base_findings = tuple(
            candidate
            for finding in confirmed
            if (
                candidate := (
                    trim_finding(text, finding)
                    if (
                        finding.source == FindingSource.RULE
                        and finding.entity_type == "PHONE_NUMBER"
                    )
                    else finding
                )
            )
            is not None
            and (allowed is None or candidate.entity_type in allowed)
        )
        if progress is not None:
            progress("ner")
        active_thresholds = thresholds or self.thresholds
        calibrated = {
            language: value
            for language, value in (language_thresholds or {}).items()
            if any(segment.language == language for segment in languages)
        }
        candidate_thresholds = _lowest_thresholds(active_thresholds, calibrated.values())
        if languages:
            raw_ner_findings = detect_with_segments(
                self.ner,
                text,
                segments=languages,
                thresholds=candidate_thresholds,
            )
        else:
            detect_for_thresholds = getattr(self.ner, "detect_with_thresholds", None)
            raw_ner_findings = (
                detect_for_thresholds(text, candidate_thresholds)
                if callable(detect_for_thresholds)
                else self.ner.detect(text)
            )
        active_business_terms = (
            self.business_terms if business_terms is None else normalize_terms(business_terms)
        )
        terms_by_language = {
            code: normalize_terms(terms)
            for code, terms in (business_terms_by_language or {}).items()
        }
        grouped: dict[str | None, list[Finding]] = defaultdict(list)
        for finding in raw_ner_findings:
            grouped[_language_at(languages, finding.start)].append(finding)
        ner_findings = tuple(
            finding
            for code, candidates in grouped.items()
            for finding in filter_ner_findings(
                text,
                candidates,
                thresholds=candidate_thresholds,
                business_terms=terms_by_language.get(code, active_business_terms),
            )
        )
        if calibrated:
            ner_findings = tuple(
                finding
                for finding in ner_findings
                if finding.score
                >= calibrated.get(
                    _language_at(languages, finding.start), active_thresholds
                ).for_entity(finding.entity_type)
            )
        if allowed is not None:
            ner_findings = tuple(
                finding for finding in ner_findings if finding.entity_type in allowed
            )
        if self.word_classes is not None:
            # Before overlap and repetition checking: A "Sie" dropped here
            # cannot trigger repetition questions later.
            ner_findings = without_non_names(
                text,
                ner_findings,
                self.word_classes,
                language_at=language_lookup(languages, self.language),
                term_check=term_check,
            )
        resolved = resolve_overlaps((*base_findings, *ner_findings))
        if on_resolved is not None:
            on_resolved(resolved)
        if progress is not None:
            progress("suspicion")
        default_starters = (
            sentence_starters if sentence_starters is not None else self.sentence_starters
        )
        if not suspicion_enabled:
            suspicious: tuple[Finding, ...] = ()
        elif languages and (sentence_starters_by_language or terms_by_language):
            suspicious = _suspicions_by_language(
                text,
                resolved,
                languages,
                sentence_starters_by_language or {},
                default_starters,
                active_business_terms,
                terms_by_language,
            )
        else:
            suspicious = find_name_suspicions(
                text,
                resolved,
                sentence_starters=default_starters,
                business_terms=active_business_terms,
            )
        # This position-accurate recall closure is not an optional
        # name heuristic: Even profiles without ``capitalized_bigram`` must
        # present a skipped occurrence of an already recognized value for
        # decision before export.
        suspicious = _with_exact_duplicate_suspicions(
            text,
            confirmed=resolved,
            suspicious=suspicious,
        )
        return DetectionResult(confirmed=resolved, suspicious=suspicious, languages=languages)


def _language_at(segments: tuple[LanguageSegment, ...], offset: int) -> str | None:
    for segment in segments:
        if segment.start <= offset < segment.end:
            return segment.language
    return segments[-1].language if segments else None


def _lowest_thresholds(
    base: EntityThresholds,
    calibrated: Iterable[EntityThresholds],
) -> EntityThresholds:
    """Candidate threshold: per type the lowest of all involved languages."""

    others = tuple(calibrated)
    if not others:
        return base
    entities = set(base.values).union(*(set(item.values) for item in others))
    return EntityThresholds(
        {entity: min(item.for_entity(entity) for item in (base, *others)) for entity in entities},
        default=min(item.default for item in (base, *others)),
    )


def _suspicions_by_language(
    text: str,
    resolved: tuple[Finding, ...],
    segments: tuple[LanguageSegment, ...],
    starters: Mapping[str, Iterable[str]],
    default_starters: Iterable[str],
    business_terms: Iterable[str],
    business_terms_by_language: Mapping[str, Iterable[str]],
) -> tuple[Finding, ...]:
    """Suspicion check per language segment with its sentence starters and terms.

    Segments end at line boundaries, and a suspicion never spans a
    line break; splitting therefore does not change the run.
    """

    found: list[Finding] = []
    defaults = tuple(default_starters)
    for segment in segments:
        piece = text[segment.start : segment.end]
        if not piece.strip():
            continue
        covered = tuple(
            Finding(
                entity_type=finding.entity_type,
                start=max(finding.start, segment.start) - segment.start,
                end=min(finding.end, segment.end) - segment.start,
                score=finding.score,
                source=finding.source,
                detector=finding.detector,
            )
            for finding in resolved
            if finding.start < segment.end and segment.start < finding.end
        )
        for finding in find_name_suspicions(
            piece,
            covered,
            sentence_starters=starters.get(segment.language, defaults),
            business_terms=business_terms_by_language.get(segment.language, business_terms),
        ):
            start = finding.start + segment.start
            end = finding.end + segment.start
            found.append(
                Finding(
                    entity_type=finding.entity_type,
                    start=start,
                    end=end,
                    score=finding.score,
                    source=finding.source,
                    text=text[start:end],
                    detector=finding.detector,
                )
            )
    return tuple(found)


def _with_exact_duplicate_suspicions(
    text: str,
    *,
    confirmed: tuple[Finding, ...],
    suspicious: tuple[Finding, ...],
) -> tuple[Finding, ...]:
    # Partially covered repetitions can generate new partial values.
    # Their occurrences also need a review decision before export.
    for _ in range(_MAX_DUPLICATE_PASSES):
        expanded = _expand_duplicate_suspicions(text, confirmed=confirmed, suspicious=suspicious)
        if expanded == suspicious:
            return expanded
        suspicious = expanded
    raise DuplicateReviewCapacityError(
        "Die Wiederholungspruefung braucht zu viele Schritte fuer ein sicheres Review."
    )


def _expand_duplicate_suspicions(
    text: str,
    *,
    confirmed: tuple[Finding, ...],
    suspicious: tuple[Finding, ...],
) -> tuple[Finding, ...]:
    """Add repetitions skipped by the model to the explicit review.

    NER evaluates each occurrence in its context. This means the same
    clear text can be recognized on page one and skipped on page two.
    An already recognized string is a strong, document-internal
    signal. Missing exact occurrences are intentionally *not* auto-
    confirmed: As suspicions, they remain individually acceptable or
    rejectable. This preserves position-accurate review decisions
    even with identical values and prevents the PDF rest-text check
    from discovering the gap only after the review is completed.
    """

    occupied = (*confirmed, *suspicious)
    if not occupied:
        return suspicious
    seeds_by_value: dict[str, list[Finding]] = {}
    for seed in occupied:
        value = text[seed.start : seed.end]
        if value.strip():
            seeds_by_value.setdefault(normalize_term(value), []).append(seed)

    unambiguous_seeds: dict[str, str] = {}
    ordered_seeds = sorted(
        seeds_by_value.items(),
        key=lambda item: (min(seed.start for seed in item[1]), item[0]),
    )
    for value, seeds in ordered_seeds:
        entity_types = {seed.entity_type for seed in seeds}
        # The same clear text can carry different types depending on context.
        # Without evidence at the new location, no type is guessed; the
        # strict rest-text check remains the fail-closed fallback.
        if len(entity_types) != 1:
            continue
        unambiguous_seeds[value] = entity_types.pop()
    if not unambiguous_seeds:
        return suspicious

    occupied_intervals = _merged_intervals(occupied)
    occupied_starts = tuple(start for start, _end in occupied_intervals)
    candidates: dict[tuple[int, int, str], Finding] = {}
    existing_duplicates = sum(item.detector == EXACT_DUPLICATE_DETECTOR for item in suspicious)
    for start, end, _value, entity_type in _iter_duplicate_matches(text, unambiguous_seeds):
        # A shorter finding can only cover part of the repetition
        # (e.g. „Wien“ in „Firmensitz Wien“). The remaining free space also needs
        # a decision, otherwise the global PDF review fails on it.
        for left, right in _uncovered_intervals(
            start, end, intervals=occupied_intervals, starts=occupied_starts
        ):
            while left < right and text[left].isspace():
                left += 1
            while right > left and text[right - 1].isspace():
                right -= 1
            if left == right:
                continue
            candidates.setdefault(
                (left, right, entity_type),
                Finding(
                    entity_type=entity_type,
                    start=left,
                    end=right,
                    score=0.25,
                    source=FindingSource.SUSPICION,
                    text=text[left:right],
                    detector=EXACT_DUPLICATE_DETECTOR,
                ),
            )
            if len(candidates) + existing_duplicates > _MAX_DUPLICATE_CANDIDATES:
                # Never truncate: all locations must go to review.
                raise DuplicateReviewCapacityError(
                    "Das Dokument enthaelt zu viele wiederholte Fundstellen fuer "
                    "ein positionsgenaues Review."
                )

    # Different recognized values can generate candidates for the
    # same previously free text area. Normal, deterministic span
    # resolution applies to these review hints as well.
    duplicates = _resolve_duplicate_overlaps(candidates.values())
    return tuple(
        sorted((*suspicious, *duplicates), key=lambda finding: (finding.start, finding.end))
    )


def iter_value_occurrences(text: str, values: Iterable[str]) -> Iterator[tuple[int, int, str]]:
    """Every occurrence of the values in the text, searched like the PDF residual-text check.

    Returns ``(start, end, key)``; values that differ only in whitespace share a key
    (``compact_value``). Anyone who masks a value must mask it everywhere the search
    finds it – otherwise export fails the residual-text check.
    """

    keys = {
        normalized: compact_value(normalized)
        for value in values
        if (normalized := normalize_term(value))
    }
    if not keys:
        return
    for start, end, _original, key in _iter_duplicate_matches(text, keys):
        yield start, end, key


def compact_value(value: str) -> str:
    """Key for a value used by ``iter_value_occurrences``."""

    return "".join(normalize_term(value).split())


def _iter_duplicate_matches(
    text: str, patterns: Mapping[str, str]
) -> Iterator[tuple[int, int, str, str]]:
    """Searches like the PDF residual text check, with offsets in the original text.

    NFKC and casefolding can change the length (ß → ss). Grapheme-based normalization
    therefore preserves the source span for each search character. Whitespace may be
    missing or appear between letters as in the export; only for such tolerant matches
    are word boundaries additionally enforced.
    """

    compact_patterns: dict[str, list[str]] = {}
    for value in patterns:
        compact_patterns.setdefault("".join(value.split()), []).append(value)
    unambiguous = {
        compact: patterns[values[0]]
        for compact, values in compact_patterns.items()
        if len({patterns[value] for value in values}) == 1
    }
    characters: list[str] = []
    offsets: list[tuple[int, int]] = []
    for cluster in regex.finditer(r"\X", text):
        for character in unicodedata.normalize("NFKC", cluster.group()).casefold():
            if not character.isspace():
                characters.append(character)
                offsets.append(cluster.span())
    compact_text = "".join(characters)
    for start, end, value, entity_type in _iter_exact_matches(compact_text, unambiguous):
        left, right = offsets[start][0], offsets[end - 1][1]
        original = text[left:right]
        direct = normalize_term(original) in compact_patterns[value]
        if not direct and (
            (
                value[0].isalnum()
                and start > 0
                and compact_text[start - 1].isalnum()
                and offsets[start - 1][1] == left
            )
            or (
                value[-1].isalnum()
                and end < len(offsets)
                and compact_text[end].isalnum()
                and offsets[end][0] == right
            )
        ):
            continue
        yield left, right, original, entity_type


def _uncovered_intervals(
    start: int,
    end: int,
    *,
    intervals: tuple[tuple[int, int], ...],
    starts: tuple[int, ...],
) -> Iterator[tuple[int, int]]:
    index = max(0, bisect_left(starts, start) - 1)
    cursor = start
    while index < len(intervals) and intervals[index][0] < end:
        left, right = intervals[index]
        if left > cursor:
            yield cursor, min(left, end)
        cursor = max(cursor, right)
        if cursor >= end:
            return
        index += 1
    if cursor < end:
        yield cursor, end


def _iter_exact_matches(
    text: str,
    patterns: Mapping[str, str],
) -> Iterator[tuple[int, int, str, str]]:
    """Finds many literal patterns with a shared set matcher."""

    if (
        len(patterns) > _MAX_DUPLICATE_PATTERNS
        or sum(len(value) for value in patterns) > _MAX_DUPLICATE_PATTERN_CHARACTERS
    ):
        raise DuplicateReviewCapacityError(
            "Das Dokument enthaelt zu viele unterschiedliche Fundstellen fuer "
            "die Wiederholungspruefung."
        )

    groups = _prefix_free_pattern_groups(patterns)
    if len(groups) > _MAX_DUPLICATE_PREFIX_DEPTH:
        raise DuplicateReviewCapacityError(
            "Die Fundstellen sind zu tief ineinander verschachtelt fuer die Wiederholungspruefung."
        )

    # Named lists yield only the longest value per start position. Therefore,
    # prefix-free groups run separately: ``AB`` remains visible even if ``ABC`` at the
    # same position cuts a occupied box. The shared time frame prevents pathological
    # literal sets from freezing the analysis worker. ``cache_pattern=False`` is a data
    # protection requirement: detected plaintext values must not enter the global regex
    # cache of the long-lived sidecar.
    deadline = monotonic() + _DUPLICATE_MATCH_TIMEOUT_SECONDS
    try:
        for group in groups:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError
            matcher = regex.compile(
                r"\L<duplicate_values>",
                duplicate_values=group,
                cache_pattern=False,
            )
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError
            for match in matcher.finditer(
                text,
                overlapped=True,
                timeout=remaining,
            ):
                if monotonic() >= deadline:
                    raise TimeoutError
                value = match.group()
                start, end = match.span()
                yield start, end, value, patterns[value]
    except TimeoutError as error:
        raise DuplicateReviewCapacityError(
            "Die Wiederholungspruefung hat ihr sicheres Zeitlimit erreicht."
        ) from error


def _prefix_free_pattern_groups(patterns: Mapping[str, str]) -> tuple[tuple[str, ...], ...]:
    """Colors literal values by prefix depth into prefix-free sets."""

    groups: list[list[str]] = []
    prefix_stack: list[str] = []
    for value in sorted(patterns):
        while prefix_stack and not value.startswith(prefix_stack[-1]):
            prefix_stack.pop()
        depth = len(prefix_stack)
        if depth == len(groups):
            groups.append([])
        groups[depth].append(value)
        prefix_stack.append(value)
    return tuple(tuple(group) for group in groups)


def _resolve_duplicate_overlaps(findings: Iterable[Finding]) -> tuple[Finding, ...]:
    """Preserves normal greedy semantics without their quadratic scan.

    All duplicate candidates have the same score. Like ``resolve_overlaps``, the longer
    span wins, then the earlier one. A Fenwick tree already marks occupied atomic text
    segments; queries and insertions thus remain limited even with tens of thousands of
    repetitions.
    """

    candidates = tuple(findings)
    if len(candidates) < 2:
        return candidates
    ranked = sorted(
        candidates,
        key=lambda finding: (
            -finding.score,
            -finding.length,
            finding.start,
            finding.end,
            finding.entity_type,
        ),
    )
    coordinates = sorted(
        {offset for finding in candidates for offset in (finding.start, finding.end)}
    )
    coordinate_index = {offset: index for index, offset in enumerate(coordinates)}
    tree = [0] * (len(coordinates) + 1)

    def add(segment: int) -> None:
        index = segment + 1
        while index < len(tree):
            tree[index] += 1
            index += index & -index

    def prefix_sum(segment_count: int) -> int:
        total = 0
        index = segment_count
        while index > 0:
            total += tree[index]
            index -= index & -index
        return total

    selected: list[Finding] = []
    for candidate in ranked:
        left = coordinate_index[candidate.start]
        right = coordinate_index[candidate.end]
        if prefix_sum(right) != prefix_sum(left):
            continue
        selected.append(candidate)
        # Selected spans never overlap each other. Therefore, each atomic segment is
        # inserted at most once over the entire run.
        for segment in range(left, right):
            add(segment)
    return tuple(sorted(selected, key=lambda finding: (finding.start, finding.end)))


def _merged_intervals(findings: Iterable[Finding]) -> tuple[tuple[int, int], ...]:
    merged: list[tuple[int, int]] = []
    for finding in sorted(findings, key=lambda item: (item.start, item.end)):
        if merged and finding.start < merged[-1][1]:
            previous_start, previous_end = merged[-1]
            merged[-1] = previous_start, max(previous_end, finding.end)
        else:
            merged.append((finding.start, finding.end))
    return tuple(merged)
