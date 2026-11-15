"""Presentation of detection results for the UI: findings, questions, and blocks.

Old and new endpoints share this presentation. The v2 API adds the category
from the catalog for each finding, and for open questions, the kind and
number in reading order. It also adds paragraph and page blocks for layout
and „Nebeneinander". The old UI ignores these additional fields.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections import Counter
from collections.abc import Mapping
from typing import Any

from .api_common import utf16_offset as _utf16_offset
from .catalog import CATEGORY_BY_ENTITY
from .detection import EXACT_DUPLICATE_DETECTOR, DetectionResult, Finding, FindingSource
from .detection.second_opinion import Assessment
from .extraction import ExtractedDocument
from .masking import counts_by_category
from .processing import FindingKind, finding_id
from .profiles import Profile, runtime_entity_type

_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n+")


def detection_for_profile(
    detection: DetectionResult,
    profile: Profile,
) -> DetectionResult:
    allowed_entities = set(profile.presidio_entities)

    def allowed(finding: Finding, *, apply_threshold: bool) -> bool:
        entity_type = runtime_entity_type(finding.entity_type)
        if entity_type not in allowed_entities:
            return False
        if str(finding.source) == "master_data" and not profile.master_data.enabled:
            return False
        # Profile confidence scores control probabilistic NER. Exact rule/check-digit
        # matches (e.g., PhoneRecognizer with a base score of 0.4) must not disappear
        # due to this; instead, they fall into review when confidence is low.
        if not apply_threshold or finding.source is not FindingSource.NER:
            return True
        threshold = profile.thresholds.for_entity(entity_type)
        if not threshold.enabled:
            return False
        minimum = threshold.needs_review
        return minimum is None or finding.score >= minimum

    confirmed = tuple(
        finding for finding in detection.confirmed if allowed(finding, apply_threshold=True)
    )
    suspicious = tuple(
        finding
        for finding in detection.suspicious
        if allowed(finding, apply_threshold=False)
        and (profile.suspect_check.enabled or finding.detector == EXACT_DUPLICATE_DETECTOR)
    )
    return DetectionResult(
        confirmed=confirmed,
        suspicious=suspicious,
        languages=detection.languages,
        assessments=detection.assessments,
    )


def finding_review_required(
    profile: Profile,
    finding: Finding,
    *,
    kind: FindingKind,
) -> bool:
    if kind is FindingKind.SUSPICIOUS:
        return True
    rule = profile.thresholds.for_entity(runtime_entity_type(finding.entity_type))
    auto_accept = rule.auto_accept
    if auto_accept is None:
        auto_accept = profile.thresholds.default.auto_accept
    return auto_accept is None or finding.score < auto_accept


def question_ids(detection: DetectionResult, profile: Profile) -> frozenset[str]:
    """Finding IDs that count as questions: every suspicion and every uncertain match."""

    ids = {finding_id(finding, kind=FindingKind.SUSPICIOUS) for finding in detection.suspicious}
    ids.update(
        finding_id(finding, kind=FindingKind.CONFIRMED)
        for finding in detection.confirmed
        if review_required(profile, finding, kind=FindingKind.CONFIRMED)
    )
    return frozenset(ids)


def text_blocks(
    text: str,
    document: ExtractedDocument | None,
    languages: DetectionResult | None = None,
) -> list[dict[str, Any]]:
    """Paragraphs (separated by blank lines) with page numbers, if the source
    knows pages.

    Since , each paragraph carries the document language at its start,
    if detection has determined languages.
    """

    page_starts: list[int] = []
    pages: list[int] = []
    if document is not None:
        for segment in sorted(document.segments, key=lambda item: item.start):
            if segment.bbox is not None:
                page = segment.bbox.page_index + 1
                if not pages or pages[-1] != page:
                    page_starts.append(segment.start)
                    pages.append(page)

    def page_at(offset: int) -> int | None:
        if not pages:
            return None
        index = bisect_right(page_starts, offset) - 1
        return pages[max(index, 0)]

    blocks: list[dict[str, Any]] = []
    position = 0
    for match in (*_PARAGRAPH_BREAK.finditer(text), None):
        end = match.start() if match is not None else len(text)
        if text[position:end].strip():
            blocks.append(
                {
                    "index": len(blocks),
                    "start": _utf16_offset(text, position),
                    "end": _utf16_offset(text, end),
                    "kind": "paragraph",
                    "page": page_at(position),
                    "language": (
                        languages.language_at(position) if languages is not None else None
                    ),
                }
            )
        if match is not None:
            position = match.end()
    return blocks


def serialize_detection(
    job_id: str,
    text: str,
    result: DetectionResult,
    *,
    profile: Profile | None = None,
    document: ExtractedDocument | None = None,
) -> dict[str, Any]:
    entries = [(finding, FindingKind.CONFIRMED) for finding in result.confirmed] + [
        (finding, FindingKind.SUSPICIOUS) for finding in result.suspicious
    ]
    # Questions are numbered in reading order per file, regardless of whether
    # they come from suspicion review or an uncertain match.
    question_numbers: dict[tuple[int, int, str, FindingKind], int] = {}
    for finding, kind in sorted(
        (item for item in entries if review_required(profile, item[0], kind=item[1])),
        key=lambda item: (item[0].start, item[0].end),
    ):
        question_numbers[(finding.start, finding.end, finding.entity_type, kind)] = (
            len(question_numbers) + 1
        )
    serialized = [
        serialize_finding(
            text,
            finding,
            kind=kind,
            profile=profile,
            question_number=question_numbers.get(
                (finding.start, finding.end, finding.entity_type, kind)
            ),
            assessments=result.assessments,
        )
        for finding, kind in entries
    ]
    confirmed = serialized[: len(result.confirmed)]
    suspicious = serialized[len(result.confirmed) :]
    by_entity = Counter(finding.entity_type for finding in (*result.confirmed, *result.suspicious))
    return {
        "job_id": job_id,
        "offset_encoding": "utf-16",
        # The extracted source text belongs to the answer because the two-column
        # preview needs it (Spec 9.3) and would find it nowhere else for PDF, DOCX,
        # or OCR. It does not leave this machine: the service listens exclusively on
        # the loopback address.
        "text": text,
        "confirmed": confirmed,
        "suspicious": suspicious,
        "findings": [*confirmed, *suspicious],
        "blocks": text_blocks(text, document, result),
        "counts": {
            "confirmed": len(confirmed),
            "suspicious": len(suspicious),
            "total": len(confirmed) + len(suspicious),
            "by_entity": dict(sorted(by_entity.items())),
            "by_category": counts_by_category(by_entity),
            "questions": len(question_numbers),
        },
    }


def review_required(profile: Profile | None, finding: Finding, *, kind: FindingKind) -> bool:
    """Whether a location requires a decision—in the mockup: a question."""

    if kind is FindingKind.SUSPICIOUS:
        return True
    if profile is None:
        return False
    return finding_review_required(profile, finding, kind=kind) or (
        runtime_entity_type(finding.entity_type) in set(profile.required_review_entities)
    )


def serialize_finding(
    text: str,
    finding: Finding,
    *,
    kind: FindingKind,
    profile: Profile | None = None,
    question_number: int | None = None,
    assessments: Mapping[str, Assessment] | None = None,
) -> dict[str, Any]:
    radius = 48
    required = review_required(profile, finding, kind=kind)
    identifier = finding_id(finding, kind=kind)
    assessment = (assessments or {}).get(identifier) if required else None
    category = CATEGORY_BY_ENTITY.get(runtime_entity_type(finding.entity_type))
    return {
        "id": identifier,
        "kind": kind.value,
        "type": finding.entity_type,
        "category": category.id if category is not None else None,
        "score": round(finding.score, 6),
        # WebView/TypeScript indiziert Strings in UTF-16-Codeeinheiten,
        # Python hingegen in Unicode-Codepoints.
        "start": _utf16_offset(text, finding.start),
        "end": _utf16_offset(text, finding.end),
        "text": text[finding.start : finding.end],
        "context": {
            "before": text[max(0, finding.start - radius) : finding.start],
            "match": text[finding.start : finding.end],
            "after": text[finding.end : finding.end + radius],
        },
        "source": str(finding.source),
        "detector": finding.detector,
        "review_required": required,
        "auto_accepted": not required,
        "question": (
            {
                "kind": "suspicion" if kind is FindingKind.SUSPICIOUS else "uncertain",
                "n": question_number,
            }
            if required and question_number is not None
            else None
        ),
        "assessment": assessment.as_dict() if assessment is not None else None,
    }
