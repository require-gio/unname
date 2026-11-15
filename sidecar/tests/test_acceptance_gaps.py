"""Targeted automated acceptance gaps.

The broad functional coverage stays in the respective domain tests. This
module only checks acceptance rules that were not previously expressed as a
single, unambiguous evidence: the master-data A/B case, the residual risk after
real processing, the missing PDF text layer, and bidirectional word boundaries
for all custom recognizers.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

import fitz
import pytest
from presidio_analyzer import EntityRecognizer

from presidio_sidecar.analysis import PlainTextAnalyzer
from presidio_sidecar.detection import DetectionPipeline, DetectionResult, Finding, FindingSource
from presidio_sidecar.deterministic_recognizers import (
    CUSTOM_ENTITY_TYPES,
    ApiKeyRecognizer,
    AtCompanyRegisterRecognizer,
    AtSocialSecurityRecognizer,
    AtUidRecognizer,
    BicRecognizer,
    CaseNumberRecognizer,
    CustomerNumberRecognizer,
    SecretRecognizer,
    StreetAddressRecognizer,
)
from presidio_sidecar.exporting import PdfRedaction, export_redacted_pdf
from presidio_sidecar.processing import (
    ProcessingMode,
    ProcessingRequest,
    ProtectionLevel,
    TextProcessor,
)
from presidio_sidecar.registry import MasterDataRegistry, RegistrySources
from presidio_sidecar.registry_crypto import generate_registry_key
from presidio_sidecar.residual_risk import ResidualRiskStatus
from presidio_sidecar.vault import TokenVault


class EmptyNer:
    detector_name = "phase-seven-empty"

    def detect(self, text: str) -> tuple[Finding, ...]:
        del text
        return ()


def _registry(tmp_path: Path, name: str) -> MasterDataRegistry:
    return MasterDataRegistry(tmp_path / f"{name}.enc", generate_registry_key())


def test_master_data_reference_case_is_confirmed_only_with_registry(
    tmp_path: Path,
) -> None:
    """Allowed control failure and mandatory hit as an A/B test."""

    text = "Tischlerei Josef Brandstetter e.U."
    empty = _registry(tmp_path, "leer")
    populated = _registry(tmp_path, "befuellt")
    persons = tmp_path / "personen.json"
    organizations = tmp_path / "organisationen.json"
    persons.write_text("[]", encoding="utf-8")
    organizations.write_text(json.dumps([text]), encoding="utf-8")
    populated.reimport(RegistrySources(persons, organizations))

    def analyze(registry: MasterDataRegistry) -> DetectionResult:
        return PlainTextAnalyzer(
            registry,
            deterministic_recognizers=(),
            semantic_pipeline=DetectionPipeline(ner=EmptyNer()),
        ).analyze(text, progress=lambda _phase: None)

    without_master_data = analyze(empty)
    with_master_data = analyze(populated)

    assert not any(
        finding.source is FindingSource.MASTER_DATA for finding in without_master_data.confirmed
    )
    assert [
        (finding.entity_type, finding.text, finding.source)
        for finding in with_master_data.confirmed
    ] == [("ORGANIZATION", text, FindingSource.MASTER_DATA)]


def test_residual_risk_after_redaction_finds_date_amount_and_article_number(
    tmp_path: Path,
) -> None:
    text = "Hans Meier bestellte am 03.04.2026 fuer 1.299,00 EUR Artikelnummer AB-90210."
    person_start = text.index("Hans Meier")
    detection = DetectionResult(
        confirmed=(
            Finding(
                "PERSON",
                person_start,
                person_start + len("Hans Meier"),
                0.99,
                FindingSource.NER,
                text="Hans Meier",
            ),
        )
    )
    vault = TokenVault(tmp_path / "state")
    try:
        result = TextProcessor(vault).apply(
            text,
            detection,
            ProcessingRequest(
                mode=ProcessingMode.MASK,
                protection_level=ProtectionLevel.DIRECT,
            ),
        )
    finally:
        vault.close()

    assert "Hans Meier" not in result.text
    assert result.residual_risk.status is ResidualRiskStatus.FOUND
    assert {str(item) for item in result.residual_risk.classes} == {
        "date_time",
        "amount_quantity",
        "reference_number",
    }
    assert result.residual_risk.blocks_export is False


def _raw_text(page: fitz.Page) -> str:
    raw = page.get_text("rawdict")
    return "".join(
        character["c"]
        for block in raw.get("blocks", [])
        for line in block.get("lines", [])
        for span in line.get("spans", [])
        for character in span.get("chars", [])
    )


def test_pdf_remove_leaves_no_markable_or_extractable_text(
    tmp_path: Path,
) -> None:
    source = tmp_path / "original.pdf"
    destination = tmp_path / "bereinigt.pdf"
    secret = "Hans Meier"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), f"Empfaenger: {secret}")
    document.save(source)
    document.close()

    with fitz.open(source) as opened:
        rectangles = opened[0].search_for(secret)
    assert len(rectangles) == 1

    export_redacted_pdf(
        source,
        destination,
        (PdfRedaction(0, tuple(rectangles[0]), secret),),
    )

    with fitz.open(destination) as exported:
        exported_page = exported[0]
        # ``search_for`` and the raw character list operate on the exact PDF text objects
        # that a viewer can select and copy.
        assert exported_page.search_for(secret) == []
        assert secret not in exported_page.get_text("text")
        assert secret not in _raw_text(exported_page)
        assert exported_page.get_pixmap().samples


CUSTOM_RECOGNIZER_CASES: tuple[
    tuple[EntityRecognizer, str, str, str, str],
    ...,
] = (
    (
        AtUidRecognizer(),
        "UID: ATU13585627",
        "ATU13585627",
        "UID: XATU13585627",
        "UID: ATU13585627X",
    ),
    (
        AtSocialSecurityRecognizer(),
        "SVNR: 7829 28 07 55",
        "7829 28 07 55",
        "SVNR: X7829 28 07 55",
        "SVNR: 7829 28 07 55X",
    ),
    (
        AtCompanyRegisterRecognizer(),
        "Firmenbuchnummer: FN 33209 m",
        "FN 33209 m",
        "Firmenbuchnummer: XFN 33209 m",
        "Firmenbuchnummer: FN 33209 mX",
    ),
    (
        BicRecognizer(),
        "BIC: BKAUATWW",
        "BKAUATWW",
        "BIC: XBKAUATWW",
        "BIC: BKAUATWWX",
    ),
    (
        CaseNumberRecognizer(),
        "Az.: 12-C-345/26",
        "12-C-345/26",
        "Az.: ä12-C-345/26",
        "Az.: 12-C-345/26ö",
    ),
    (
        CustomerNumberRecognizer(),
        "Kundennummer: KD-4711",
        "KD-4711",
        "Kundennummer: äKD-4711",
        "Kundennummer: KD-4711ö",
    ),
    (
        ApiKeyRecognizer(),
        "API-Key: sk-abcdefghijklmnopqrstuvwxyz1234",
        "sk-abcdefghijklmnopqrstuvwxyz1234",
        "API-Key: äsk-abcdefghijklmnopqrstuvwxyz1234",
        "API-Key: sk-abcdefghijklmnopqrstuvwxyz1234ö",
    ),
    (
        SecretRecognizer(),
        "sk-abcdefghijklmnopqrstuvwxyz1234",
        "sk-abcdefghijklmnopqrstuvwxyz1234",
        "äsk-abcdefghijklmnopqrstuvwxyz1234",
        "sk-abcdefghijklmnopqrstuvwxyz1234ö",
    ),
    (
        StreetAddressRecognizer(),
        "Anschrift: Hauptstraße 12",
        "Hauptstraße 12",
        "Anschrift: äHauptstraße 12",
        "Anschrift: Hauptstraße 12ö",
    ),
)


def _matched_texts(recognizer: EntityRecognizer, text: str) -> list[str]:
    results = recognizer.analyze(text, recognizer.supported_entities)
    return [text[result.start : result.end] for result in results]


def _covered_entities(cases: Iterable[tuple[EntityRecognizer, str, str, str, str]]) -> set[str]:
    return {entity for recognizer, *_rest in cases for entity in recognizer.supported_entities}


def test_word_boundary_matrix_covers_every_custom_recognizer() -> None:
    assert _covered_entities(CUSTOM_RECOGNIZER_CASES) == set(CUSTOM_ENTITY_TYPES)


@pytest.mark.parametrize(
    ("recognizer", "valid_text", "expected", "left_embedded", "right_embedded"),
    CUSTOM_RECOGNIZER_CASES,
    ids=[case[0].supported_entities[0] for case in CUSTOM_RECOGNIZER_CASES],
)
def test_every_custom_recognizer_respects_both_word_boundaries(
    recognizer: EntityRecognizer,
    valid_text: str,
    expected: str,
    left_embedded: str,
    right_embedded: str,
) -> None:
    assert _matched_texts(recognizer, valid_text) == [expected]
    assert _matched_texts(recognizer, left_embedded) == []
    assert _matched_texts(recognizer, right_embedded) == []
