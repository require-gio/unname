"""Regression test: billing export from a cloud platform as XLSX (October 2026).

According to the reference solution the sheet contains only dates that need masking.
Previously the rules treated decimal parts as separate numbers:
"0.0308708" became phone number "0308708", "2.4388733333333334" a credit card,
"0.2817504" a phone number. Date and hour before the time ("2026-03-27 20:00:00")
were sometimes recognized as a date, sometimes—if the checksum matched— as an Austrian
social security number. The fixture is the sheet in TSV format with account and workspace
IDs replaced; the test rebuilds an XLSX so that cell extraction is also exercised.
"""

from __future__ import annotations

from collections.abc import Iterator
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import Workbook

from presidio_sidecar.analysis import PlainTextAnalyzer
from presidio_sidecar.base_profile import base_profile
from presidio_sidecar.config import Settings
from presidio_sidecar.detection import DetectionPipeline, DetectionResult, Finding
from presidio_sidecar.diagnostics import DiagnosticsRecorder
from presidio_sidecar.extraction.extractor import extract_bytes
from presidio_sidecar.findings_view import review_required
from presidio_sidecar.processing import FindingKind
from presidio_sidecar.registry import MasterDataRegistry
from presidio_sidecar.registry_crypto import generate_registry_key
from presidio_sidecar.services import PhaseOneServices

FIXTURE = (
    Path(__file__).resolve().parents[2] / "testdata" / "regression" / "abrechnungsexport-cloud.tsv"
)
#: Columns that contain dates or timestamps; ``usage_quantity`` holds numeric values.
DATE_COLUMNS = ("usage_start_time", "usage_end_time", "usage_date", "ingestion_date")
NUMBER_COLUMN = "usage_quantity"


class EmptyNer:
    detector_name = "test-empty"

    def detect(self, text: str) -> tuple[Finding, ...]:
        return ()


def _rows() -> list[list[str]]:
    return [line.split("\t") for line in FIXTURE.read_text(encoding="utf-8").splitlines()]


def _cell(value: str, *, number: bool) -> str | int | float:
    if not number:
        return value
    return int(value) if value.isdigit() else float(value)


def _workbook(rows: list[list[str]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "data"
    number_index = rows[0].index(NUMBER_COLUMN)
    sheet.append(rows[0])
    for row in rows[1:]:
        sheet.append(
            [_cell(value, number=index == number_index) for index, value in enumerate(row)]
        )
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


@pytest.fixture(scope="module")
def analyzed(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, DetectionResult]]:
    tmp_path = tmp_path_factory.mktemp("abrechnungsexport")
    registry = MasterDataRegistry(tmp_path / "global.enc", generate_registry_key())
    services = PhaseOneServices(
        Settings(state_directory=tmp_path / "state"),
        DiagnosticsRecorder(),
        registry=registry,
        analyzer=PlainTextAnalyzer(registry, semantic_pipeline=DetectionPipeline(ner=EmptyNer())),
    )
    services.second_opinion_model = lambda: None  # type: ignore[method-assign]
    text = extract_bytes(_workbook(_rows()), filename="abrechnung.xlsx").text
    try:
        yield (
            text,
            services.analyzer_for_profile(base_profile()).analyze(text, progress=lambda _: None),
        )
    finally:
        services.close()


def test_numbers_with_decimals_not_detected(
    analyzed: tuple[str, DetectionResult],
) -> None:
    """No phone, account, or card numbers detected, even as questions."""

    _, result = analyzed
    numeric = [
        (finding.entity_type, finding.text)
        for finding in (*result.confirmed, *result.suspicious)
        if finding.entity_type != "DATE_TIME" and not any(c.isalpha() for c in finding.text or "")
    ]

    assert numeric == []


def test_every_date_is_detected_as_date(analyzed: tuple[str, DetectionResult]) -> None:
    """All date cells recognized as dates, none misidentified as ID numbers."""

    _, result = analyzed
    rows = _rows()
    indices = [rows[0].index(column) for column in DATE_COLUMNS]
    expected = sorted(row[index][:10] for row in rows[1:] for index in indices)
    dates = sorted(
        finding.text or "" for finding in result.confirmed if finding.entity_type == "DATE_TIME"
    )

    assert dates == expected
    assert {finding.entity_type for finding in result.confirmed} == {"DATE_TIME"}


def test_without_models_only_term_remains_as_question(
    analyzed: tuple[str, DetectionResult],
) -> None:
    """The tag "Predictive Optimization" clarifies the second opinion (a term, not a name)."""

    _, result = analyzed
    profile = base_profile()
    questions = [
        (finding.entity_type, finding.text)
        for finding in result.suspicious
        if review_required(profile, finding, kind=FindingKind.SUSPICIOUS)
    ]

    assert questions == [
        ("PERSON", "Predictive Optimization"),
        ("PERSON", "Predictive Optimization"),
    ]
