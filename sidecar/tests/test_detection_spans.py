"""The three offset edge cases from spec §7.7 are explicitly covered."""

from __future__ import annotations

import pytest

from presidio_sidecar.detection import (
    Finding,
    FindingSource,
    SpanReplacement,
    replace_back_to_front,
    resolve_overlaps,
    trim_finding,
)


def test_span_trimming_keeps_whitespace_and_punctuation_in_document() -> None:
    text = "Vorher ( Hans Meier, ) nachher"
    start = text.index("(")
    end = text.index(")") + 1
    raw = Finding("PERSON", start, end, 0.8, FindingSource.NER, text=text[start:end])

    trimmed = trim_finding(text, raw)

    assert trimmed is not None
    assert trimmed.text == "Hans Meier"
    assert text[: trimmed.start].endswith("( ")
    assert text[trimmed.end :].startswith(", )")


def test_span_trimming_keeps_balanced_phone_parentheses() -> None:
    text = "(030) 1234567"
    finding = Finding("PHONE_NUMBER", 0, len(text), 0.8, FindingSource.RULE)

    trimmed = trim_finding(text, finding)

    assert trimmed is not None
    assert trimmed.text == text


def test_span_trimming_removes_single_cut_off_parenthesis() -> None:
    text = "(+43 1 2345678"
    finding = Finding("PHONE_NUMBER", 0, len(text), 0.8, FindingSource.RULE)

    trimmed = trim_finding(text, finding)

    assert trimmed is not None
    assert trimmed.text == "+43 1 2345678"


def test_on_overlap_highest_score_wins_not_longest_span() -> None:
    text = "Meier Spielwaren GmbH"
    organization = Finding(
        "ORGANIZATION",
        0,
        len(text),
        0.70,
        FindingSource.NER,
        text=text,
    )
    person = Finding("PERSON", 0, 5, 0.95, FindingSource.MASTER_DATA, text="Meier")

    assert resolve_overlaps((organization, person)) == (person,)


def test_replacement_from_back_to_front_keeps_both_original_offsets() -> None:
    text = "Hans Meier traf Petra Novak."
    hans = Finding("PERSON", 0, 10, 0.9, FindingSource.NER, text="Hans Meier")
    petra_start = text.index("Petra")
    petra = Finding(
        "PERSON",
        petra_start,
        petra_start + len("Petra Novak"),
        0.9,
        FindingSource.NER,
        text="Petra Novak",
    )

    result = replace_back_to_front(
        text,
        (
            SpanReplacement.for_finding(hans, "<<PERSON_1>>"),
            SpanReplacement.for_finding(petra, "<<PERSON_2>>"),
        ),
    )

    assert result == "<<PERSON_1>> traf <<PERSON_2>>."


def test_overlapping_replacement_spans_are_rejected() -> None:
    with pytest.raises(ValueError, match="Ueberlappende"):
        replace_back_to_front(
            "Hans Meier",
            (
                SpanReplacement(0, 10, "PERSON"),
                SpanReplacement(5, 10, "NACHNAME"),
            ),
        )
