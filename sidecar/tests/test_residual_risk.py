"""Feature classes of the heuristic residual risk check."""

from __future__ import annotations

import pytest

from presidio_sidecar.residual_risk import ResidualRiskAnalyzer, ResidualRiskStatus


@pytest.mark.parametrize(
    ("text", "expected_class"),
    [
        ("Termin am 03.04.2026 um 14:30 Uhr", "date_time"),
        ("Geliefert werden 500 Stück zum Preis von 1.299,00 EUR.", "amount_quantity"),
        ("Artikelnummer AB-90210 wurde verbucht.", "reference_number"),
        ("Die Lieferung geht nach Wien.", "location_region"),
        ("Zuständig ist die Gefahrgutbeauftragte.", "rare_term"),
    ],
)
def test_each_residual_risk_class_is_detected(text: str, expected_class: str) -> None:
    result = ResidualRiskAnalyzer().analyze(text)

    assert expected_class in {str(item) for item in result.classes}
    assert result.status is ResidualRiskStatus.CLEAR


@pytest.mark.parametrize(
    "text",
    [
        "Version 3.4.12 ist installiert.",
        "Artikel ohne Nummer ist lagernd.",
        "Das Wort Stückliste enthält keine Menge.",
        "Der Platzhalter [Person] bleibt sichtbar.",
        "<<PERSON_deadbeef>> und " + "a" * 64 + " sowie ••••••••",
    ],
)
def test_near_misses_and_replacement_artifacts_do_not_count(text: str) -> None:
    result = ResidualRiskAnalyzer().analyze(text)

    assert result.classes == ()
    assert result.status is ResidualRiskStatus.CLEAR


def test_two_classes_trigger_a_finding_but_no_export_block() -> None:
    result = ResidualRiskAnalyzer().analyze(
        "Am 03.04.2026 wurden 500 Stück unter Artikelnummer AB-90210 geliefert."
    )
    payload = result.as_dict()

    assert result.status is ResidualRiskStatus.FOUND
    assert payload["class_count"] == 3
    assert payload["found"] is True
    assert payload["blocks_export"] is False
    assert payload["kind"] == "heuristic"
    assert "ersetzt keine menschliche Bewertung" in str(payload["notice"])


def test_exactly_two_classes_reach_the_threshold() -> None:
    result = ResidualRiskAnalyzer().analyze("Am 03.04.2026 wurden 500 Stück geliefert.")

    assert result.status is ResidualRiskStatus.FOUND
    assert len(result.classes) == 2


def test_long_values_and_bracketed_places_are_not_guessed_as_replacements() -> None:
    result = ResidualRiskAnalyzer().analyze(
        "Artikelnummer ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890, Ziel [Wien]."
    )

    assert {str(item) for item in result.classes} == {
        "reference_number",
        "location_region",
    }


def test_only_exact_processor_reported_replacement_spans_are_hidden() -> None:
    text = "Artikelnummer " + "a" * 64 + ", am 03.04.2026."
    replacement_start = text.index("a" * 64)
    result = ResidualRiskAnalyzer().analyze(
        text,
        replacement_spans=[(replacement_start, replacement_start + 64)],
    )

    assert {str(item) for item in result.classes} == {"date_time"}


@pytest.mark.parametrize("text", ["1500 Stück", "1299 EUR"])
def test_quantities_and_amounts_are_not_postal_codes(text: str) -> None:
    result = ResidualRiskAnalyzer().analyze(text)

    assert {str(item) for item in result.classes} == {"amount_quantity"}


def test_smaller_locations_and_rare_professions_are_covered() -> None:
    result = ResidualRiskAnalyzer().analyze(
        "Die Neurochirurgin arbeitet am 03.04.2026 in Hallstatt."
    )

    assert {str(item) for item in result.classes} == {
        "date_time",
        "location_region",
        "rare_term",
    }


def test_multiple_hits_of_one_class_remain_one_class() -> None:
    result = ResidualRiskAnalyzer().analyze("03.04.2026 und 04.04.2026 und 14:30 Uhr")

    assert [str(item) for item in result.classes] == ["date_time"]
    assert result.status is ResidualRiskStatus.CLEAR
    assert result.counts[result.classes[0]] == 3


def test_remaining_entities_supplement_the_text_scanner() -> None:
    result = ResidualRiskAnalyzer().analyze(
        "Keine Ortsliste im Scanner.",
        remaining_entity_types=["LOCATION", "DATE_TIME"],
    )

    assert result.status is ResidualRiskStatus.FOUND
    assert {str(item) for item in result.classes} == {"date_time", "location_region"}
