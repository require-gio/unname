"""Field identifiers before rule matches: context boosts, opposite context rejects."""

from __future__ import annotations

import pytest

from presidio_sidecar.detection import Finding, FindingSource, apply_rule_context
from presidio_sidecar.detection.context import CONTEXT_BOOST, LABEL_SCORE

PHONE_CONTEXT = ("telefon", "tel", "mobil", "phone")
DATE_CONTEXT = ("datum", "rechnungsdatum")
TAX_CONTEXT = ("steuernummer",)


def _finding(text: str, value: str, entity_type: str, score: float) -> Finding:
    start = text.index(value)
    return Finding(
        entity_type,
        start,
        start + len(value),
        score,
        FindingSource.RULE,
        text=value,
        detector="test",
    )


@pytest.mark.parametrize(
    "text",
    [
        "Tel: 050123/154-100",
        "Tel.: 050123/154-100",
        "Tel.-Nr. 050123/154-100",
        "Kundentelefon 050123/154-100",
        "Telefon:\n050123/154-100",
    ],
)
def test_label_right_before_number_makes_it_unambiguous(text: str) -> None:
    finding = _finding(text, "050123/154-100", "PHONE_NUMBER", 0.4)

    result = apply_rule_context(text, finding, PHONE_CONTEXT)

    assert result is not None
    assert result.score == LABEL_SCORE


def test_context_nearby_boosts_presidio_score() -> None:
    text = "Telefon tagsüber bitte 050123/154-100"
    finding = _finding(text, "050123/154-100", "PHONE_NUMBER", 0.4)

    result = apply_rule_context(text, finding, PHONE_CONTEXT)

    assert result is not None
    assert result.score == pytest.approx(0.4 + CONTEXT_BOOST)


def test_context_on_earlier_line_does_not_count() -> None:
    text = "Tel: 0664 1234567\nSonstiges\n050123/154-100"
    finding = _finding(text, "050123/154-100", "PHONE_NUMBER", 0.4)

    assert apply_rule_context(text, finding, PHONE_CONTEXT) == finding


def test_short_context_words_not_counted_as_word_end() -> None:
    # "Hotel" ends with "tel" but is not a phone identifier.
    text = "Hotel 050123/154-100"
    finding = _finding(text, "050123/154-100", "PHONE_NUMBER", 0.4)

    assert apply_rule_context(text, finding, PHONE_CONTEXT) == finding


@pytest.mark.parametrize(
    "text",
    ["Auftragsdatum: 15.07.2026", "Rechnungsdatum: 15.07.2026", "Datum 15.07.2026"],
)
def test_date_identifier_as_composite(text: str) -> None:
    finding = _finding(text, "15.07.2026", "DATE_TIME", 0.6)

    result = apply_rule_context(text, finding, DATE_CONTEXT)

    assert result is not None
    assert result.score == LABEL_SCORE


def test_score_at_most_one() -> None:
    text = "Tel: 050123/154-100"
    finding = _finding(text, "050123/154-100", "PHONE_NUMBER", 0.99)

    result = apply_rule_context(text, finding, PHONE_CONTEXT)

    assert result is not None
    assert result.score == 1.0


@pytest.mark.parametrize(
    ("text", "entity_type"),
    [
        ("BestellNr: 1002607543256", "DE_TAX_NUMBER"),
        ("Bestell-Nr. 1002607543256", "DE_TAX_NUMBER"),
        ("Artikelnummer 1002607543256", "PHONE_NUMBER"),
        ("Order No. 1002607543256", "PHONE_NUMBER"),
    ],
)
def test_foreign_number_identifier_refutes_phone_and_tax_number(
    text: str, entity_type: str
) -> None:
    finding = _finding(text, "1002607543256", entity_type, 0.5)

    assert apply_rule_context(text, finding, TAX_CONTEXT + PHONE_CONTEXT) is None


def test_foreign_number_identifier_keeps_other_types() -> None:
    text = "Rechnungsnummer: max@example.at"
    finding = _finding(text, "max@example.at", "EMAIL_ADDRESS", 1.0)

    assert apply_rule_context(text, finding, ("email",)) == finding


def test_article_number_with_variant_not_phone_number() -> None:
    text = "1,00\n04960008/02\n199,00"
    finding = _finding(text, "04960008", "PHONE_NUMBER", 0.4)

    assert apply_rule_context(text, finding, PHONE_CONTEXT) is None


def test_number_without_context_remains_uncertain() -> None:
    text = "Rückruf unter 0664 1234567 erbeten"
    finding = _finding(text, "0664 1234567", "PHONE_NUMBER", 0.4)

    assert apply_rule_context(text, finding, PHONE_CONTEXT) == finding


@pytest.mark.parametrize(
    "text",
    ["MUSTERGASSE 12/1/30", "Hauptstraße 12/1/30", "Musterstr. 12/1/30", "Am Ring 12/1/30"],
)
def test_stair_and_door_after_street_are_not_a_date(text: str) -> None:
    finding = _finding(text, "12/1/30", "DATE_TIME", 0.6)

    assert apply_rule_context(text, finding, DATE_CONTEXT) is None


def test_date_with_identifier_remains_after_street_word() -> None:
    text = "Umzug Hauptstraße, Datum: 12/1/30"
    finding = _finding(text, "12/1/30", "DATE_TIME", 0.6)

    result = apply_rule_context(text, finding, DATE_CONTEXT)

    assert result is not None
    assert result.score == LABEL_SCORE


@pytest.mark.parametrize(
    ("text", "value", "entity_type"),
    [
        ("DSU\t0.0308708\tAPI", "0308708", "PHONE_NUMBER"),
        ("DSU\t0.2817504\tAPI", "0.2817504", "PHONE_NUMBER"),
        ("DBU\t2.4388733333333334\tCOMPUTE", "4388733333333334", "CREDIT_CARD"),
        ("Betrag 1.234,0301234567", "0301234567", "PHONE_NUMBER"),
        ("AWS\t2026-03-27 20:00:00\t2026-03-27", "2026-03-27 20", "AT_SVNR"),
    ],
)
def test_part_of_decimal_or_time_not_number(text: str, value: str, entity_type: str) -> None:
    finding = _finding(text, value, entity_type, 1.0)

    assert apply_rule_context(text, finding, PHONE_CONTEXT) is None


@pytest.mark.parametrize(
    "text",
    [
        "Tel: 0301234567.",
        "Tel: 0301234567, Fax: 0301234568",
        "Rückruf um 10:30 unter 0301234567",
    ],
)
def test_punctuation_after_number_not_decimal_part(text: str) -> None:
    finding = _finding(text, "0301234567", "PHONE_NUMBER", 0.4)

    assert apply_rule_context(text, finding, PHONE_CONTEXT) is not None


def test_time_remains_date() -> None:
    text = "Termin 2026-03-27 20:00"
    finding = _finding(text, "2026-03-27 20:00", "DATE_TIME", 0.6)

    assert apply_rule_context(text, finding, DATE_CONTEXT) is not None
