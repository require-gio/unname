"""Regression: Delivery note from a furniture store (October 2026).

Previously almost every number was treated as a name – phone numbers after “Tel:”, an
article number as a phone number, the order number as a tax ID, stair and door
parts of the address as a date – and phrases like “Ihr Online Team” were considered
possible names. The fixture contains the extracted PDF text with personal and
company data replaced; line layout and numeric formats are unchanged.

The NER component is left empty so the case runs without models. Upper-case names must be
reported by the suspicion check – this is intentional.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from presidio_sidecar.analysis import PlainTextAnalyzer
from presidio_sidecar.base_profile import base_profile
from presidio_sidecar.config import Settings
from presidio_sidecar.detection import DetectionPipeline, DetectionResult, Finding
from presidio_sidecar.diagnostics import DiagnosticsRecorder
from presidio_sidecar.findings_view import review_required
from presidio_sidecar.processing import FindingKind
from presidio_sidecar.registry import MasterDataRegistry
from presidio_sidecar.registry_crypto import generate_registry_key
from presidio_sidecar.services import PhaseOneServices

FIXTURE = (
    Path(__file__).resolve().parents[2] / "testdata" / "regression" / "lieferschein-moebelhaus.txt"
)


class EmptyNer:
    detector_name = "test-empty"

    def detect(self, text: str) -> tuple[Finding, ...]:
        return ()


@pytest.fixture(scope="module")
def analyzed(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, DetectionResult]]:
    tmp_path = tmp_path_factory.mktemp("lieferschein")
    registry = MasterDataRegistry(tmp_path / "global.enc", generate_registry_key())
    services = PhaseOneServices(
        Settings(state_directory=tmp_path / "state"),
        DiagnosticsRecorder(),
        registry=registry,
        analyzer=PlainTextAnalyzer(registry, semantic_pipeline=DetectionPipeline(ner=EmptyNer())),
    )
    # No models and no second opinion: expectations apply to the rules and the
    # suspicion check alone, consistently on every machine.
    services.second_opinion_model = lambda: None  # type: ignore[method-assign]
    text = FIXTURE.read_text(encoding="utf-8")
    try:
        yield (
            text,
            services.analyzer_for_profile(base_profile()).analyze(text, progress=lambda _: None),
        )
    finally:
        services.close()


def _questions(result: DetectionResult) -> list[tuple[str, str]]:
    profile = base_profile()
    return [
        (finding.entity_type, finding.text or "")
        for kind, findings in (
            (FindingKind.CONFIRMED, result.confirmed),
            (FindingKind.SUSPICIOUS, result.suspicious),
        )
        for finding in findings
        if review_required(profile, finding, kind=kind)
    ]


def test_phone_numbers_after_tel_are_accepted_without_question(
    analyzed: tuple[str, DetectionResult],
) -> None:
    _, result = analyzed
    phones = [finding.text for finding in result.confirmed if finding.entity_type == "PHONE_NUMBER"]

    assert phones == [
        "050123/154-100",
        "+436641234567",
        "+43 (0) 50123-154100",
        "050123/154-100",
    ]
    assert not any(entity == "PHONE_NUMBER" for entity, _ in _questions(result))


def test_date_values_after_their_label_are_unambiguous(
    analyzed: tuple[str, DetectionResult],
) -> None:
    _, result = analyzed
    dates = [finding.text for finding in result.confirmed if finding.entity_type == "DATE_TIME"]

    assert dates == ["16.07.2026", "15.07.2026", "16.07.2026"]
    assert not any(entity == "DATE_TIME" for entity, _ in _questions(result))


@pytest.mark.parametrize(
    "value",
    [
        "04960008",  # Article number "04960008/02"
        "1002607543256",  # "OrderNo:"
        "12/1/30",  # Stair/door behind "SAMPLE STREET"
    ],
)
def test_unrelated_numbers_are_not_detected(
    analyzed: tuple[str, DetectionResult], value: str
) -> None:
    _, result = analyzed

    assert value not in {finding.text for finding in (*result.confirmed, *result.suspicious)}


def test_only_real_names_remain_as_questions(analyzed: tuple[str, DetectionResult]) -> None:
    """Without NER the suspicion check reports only the names."""

    _, result = analyzed

    assert _questions(result) == [
        ("PERSON", "MAX MUSTERMANN"),
        ("ORGANIZATION", "Moebelhaus Beispiel KG"),
        ("PERSON", "MUSTERMANN MAX"),
    ]
