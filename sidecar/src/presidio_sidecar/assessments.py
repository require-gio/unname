"""Second opinion as a stage after suspicion review (spec §7.1).

The stage only shows questions for a detection – suspicious cases and
uncertain hits, as the base profile shows – and only the types the
classifier can judge (``ASSESSED_TYPES``). It does not modify the finding;
it attaches an assessment per question
(``DetectionResult.assessments``). What happens next is decided by policy:
a suggestion in the question card or a decision “from the local AI” that
remains visible and reversible.

If the model fails, the job continues without assessment: a second
opinion must never block a detection.

An exception to “does not modify the finding” is ``term_check_for``: it
runs during detection and only for NER persons and companies that the
word-class tagger does not treat as proper nouns. If both tagger *and*
AI label it as a “term”, the spot is not a finding.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from .detection import DetectionResult, Finding
from .detection.pipeline import EXACT_DUPLICATE_DETECTOR, _with_exact_duplicate_suspicions
from .detection.second_opinion import (
    ASSESSED_TYPES,
    MAX_QUESTIONS,
    PROTECT_LABELS,
    PROTECT_MINIMUM,
    Policy,
    SecondOpinionModel,
    SecondOpinionUnavailableError,
    assess,
    classifier_input,
)
from .detection.suspicion import NAME_SUSPICION_DETECTOR
from .detection.word_classes import TermCheck
from .detection.wordlists import normalize_term
from .findings_view import detection_for_profile, question_ids
from .processing import FindingKind, finding_id
from .profiles import Profile


@dataclass(frozen=True, slots=True)
class SecondOpinionOptions:
    """Second opinion settings; part of the text cache key."""

    policy: Policy
    keep_threshold: float

    def fingerprint(self) -> str:
        return f"{self.policy}:{self.keep_threshold:.4f}"


def term_check_for(
    model: SecondOpinionModel,
    options: SecondOpinionOptions,
) -> TermCheck | None:
    """Second opinion for NER hits that are not proper nouns by their word class.

    Unlike the question stage, it decides *before* review whether such
    a hit is one at all: „Verbraucher" with a GLiNER score of 0.99
    would otherwise be masked without any question. It only acts where the AI
    may decide itself according to policy, and only from the same confidence
    (``PROTECT_MINIMUM``). The tagger alone is never sufficient
    (``detection/word_classes.py``).
    """

    if options.policy == "suggest":
        return None

    def check(text: str, findings: Sequence[Finding]) -> list[bool]:
        chosen = tuple(findings)[:MAX_QUESTIONS]
        inputs = [
            classifier_input(
                text,
                finding.start,
                finding.end,
                entity_type=finding.entity_type,
                suspicion=False,
            )
            for finding in chosen
        ]
        try:
            # Individually: int8 quantization determines its scale over the
            # entire batch. In a batch, the judgment on „Monat Juni"
            # depends on what else is in the document (0.62 alone, 0.79 to
            # 0.89 in a batch). A decision without a question must
            # be reproducible; the number of such spots is small.
            probabilities = model.probabilities(inputs, batch_size=1)
        except SecondOpinionUnavailableError:
            return []
        return [
            1.0 - sum(values[: len(PROTECT_LABELS)]) >= PROTECT_MINIMUM for values in probabilities
        ]

    return check


def with_assessments(
    text: str,
    detection: DetectionResult,
    profile: Profile,
    model: SecondOpinionModel,
    options: SecondOpinionOptions,
    *,
    diagnostics: Any = None,
) -> DetectionResult:
    """Attach the local AI's assessment to each assessable question."""

    visible = detection_for_profile(detection, profile)
    questions = question_ids(visible, profile)
    candidates: list[tuple[str, str]] = []
    heuristic: set[str] = set()
    seeds = _duplicate_seeds(text, detection)
    for kind, findings in (
        (FindingKind.CONFIRMED, visible.confirmed),
        (FindingKind.SUSPICIOUS, visible.suspicious),
    ):
        for finding in findings:
            identifier = finding_id(finding, kind=kind)
            if identifier not in questions or finding.entity_type not in ASSESSED_TYPES:
                continue
            if kind is FindingKind.SUSPICIOUS and (
                finding.detector == NAME_SUSPICION_DETECTOR
                or (
                    finding.detector == EXACT_DUPLICATE_DETECTOR
                    and _repeats_only_capitalization(text[finding.start : finding.end], seeds)
                )
            ):
                heuristic.add(identifier)
            candidates.append(
                (
                    identifier,
                    classifier_input(
                        text,
                        finding.start,
                        finding.end,
                        entity_type=finding.entity_type,
                        suspicion=kind is FindingKind.SUSPICIOUS,
                    ),
                )
            )
    candidates = candidates[:MAX_QUESTIONS]
    if not candidates:
        return detection
    try:
        probabilities = model.probabilities([item for _identifier, item in candidates])
        version = model.version
    except SecondOpinionUnavailableError as error:
        if diagnostics is not None:
            diagnostics.record(
                "model_load",
                "Die Zweitmeinung ist nicht verfuegbar; die Fragen bleiben ohne Einschaetzung.",
                severity="warning",
                component="classifier",
                error_type=type(error).__name__,
            )
        return detection
    assessments = {
        identifier: assess(
            values,
            policy=options.policy,
            keep_threshold=options.keep_threshold,
            model=version,
            heuristic=identifier in heuristic,
        )
        for (identifier, _input), values in zip(candidates, probabilities, strict=True)
    }
    classified = _reclassified(replace(detection, assessments=assessments))
    if classified.suspicious == detection.suspicious:
        return classified
    # Derived repetitions inherit the type of their source. After
    # reclassification, the independent judgments on their
    # sub-spans must not produce a different category (e.g., "GE KOS" as a company,
    # while the complete source remains a name suspicion).
    original = tuple(
        item for item in classified.suspicious if item.detector != EXACT_DUPLICATE_DETECTOR
    )
    rebuilt = _with_exact_duplicate_suspicions(
        text, confirmed=classified.confirmed, suspicious=original
    )
    occupied = sorted((*classified.confirmed, *rebuilt), key=lambda item: item.start)
    starts = [item.start for item in occupied]
    # Integrations can provide isolated repetition hints. Their
    # review/judgment is preserved if no new derived spot replaces
    # it; a reclassification must never reduce coverage.
    orphaned: list[Finding] = []
    for item in classified.suspicious:
        if item.detector != EXACT_DUPLICATE_DETECTOR:
            continue
        before = bisect_left(starts, item.end)
        if not before or occupied[before - 1].end <= item.start:
            orphaned.append(item)
    rebuilt = tuple(sorted((*rebuilt, *orphaned), key=lambda item: (item.start, item.end)))
    retained_ids = {finding_id(item, kind=FindingKind.SUSPICIOUS) for item in rebuilt}
    retained_ids.update(
        finding_id(item, kind=FindingKind.CONFIRMED) for item in classified.confirmed
    )
    return replace(
        classified,
        suspicious=rebuilt,
        assessments={
            key: value for key, value in classified.assessments.items() if key in retained_ids
        },
    )


@dataclass(frozen=True, slots=True)
class _DuplicateSeeds:
    #: Values from uppercase suspicions.
    capitalization: tuple[str, ...]
    #: Values from everything else: model, rule, master data, name lists.
    evidenced: tuple[str, ...]


def _duplicate_seeds(text: str, detection: DetectionResult) -> _DuplicateSeeds:
    capitalization: list[str] = []
    evidenced: list[str] = []
    for finding in (*detection.confirmed, *detection.suspicious):
        if finding.detector == EXACT_DUPLICATE_DETECTOR:
            continue
        value = normalize_term(text[finding.start : finding.end])
        (capitalization if finding.detector == NAME_SUSPICION_DETECTOR else evidenced).append(value)
    return _DuplicateSeeds(tuple(capitalization), tuple(evidenced))


def _repeats_only_capitalization(value: str, seeds: _DuplicateSeeds) -> bool:
    """A repetition for which only the capitalization of its source speaks.

    A repetition inherits the evidence of its source. If it stems demonstrably
    only from a capitalization suspicion ("Zuständige Aufsichtsbehörde" (responsible supervisory
    authority) →
    "zuständige Aufsichtsbehörde" (responsible supervisory authority), it is no more than such a
    suspicion, and the AI may keep it as well. Any other or unknown
    source makes it a real repetition. A repetition can be part of
    its source (the free remainder next to a shorter hit);
    containment is therefore sufficient.
    """

    normalized = normalize_term(value)
    return any(normalized in item for item in seeds.capitalization) and not any(
        normalized in item for item in seeds.evidenced
    )


#: What a name suspicion becomes instead after the AI's certain judgment.
_SUSPICION_TYPES = {"organization_or_product": "ORGANIZATION", "location": "LOCATION"}


def _reclassified(detection: DetectionResult) -> DetectionResult:
    """A name suspicion that the AI reads as a company, product, or place with certainty
    becomes that.
    becomes that.

    The suspicion review knows only uppercase; whether "Claude Code" is
    a name or a product is known only to the AI. From the same confidence, from
    which it decides towards protection (``PROTECT_MINIMUM``), the
    spot belongs to the category of its label – it is then masked if
    the task masks companies or places. Below that, it remains a name suspicion.
    """

    assessments = dict(detection.assessments)
    suspicious: list[Finding] = []
    for finding in detection.suspicious:
        identifier = finding_id(finding, kind=FindingKind.SUSPICIOUS)
        assessment = assessments.get(identifier)
        target = _SUSPICION_TYPES.get(assessment.label) if assessment is not None else None
        if (
            assessment is None
            or target is None
            or finding.entity_type != "PERSON"
            or assessment.confidence < PROTECT_MINIMUM
        ):
            suspicious.append(finding)
            continue
        moved = replace(finding, entity_type=target)
        del assessments[identifier]
        assessments[finding_id(moved, kind=FindingKind.SUSPICIOUS)] = assessment
        suspicious.append(moved)
    return replace(detection, suspicious=tuple(suspicious), assessments=assessments)
